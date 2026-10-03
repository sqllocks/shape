"""W7-04 item 7: ``profile_show``, ``contract_validate`` and ``safe_scan``."""

from __future__ import annotations

import json
import zipfile

import pytest
from data_1_1 import EMAILS, shop_tables, write_dataset

from shape.cli.main import main


def cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def shop(tmp_path, api11):
    """A profile of the shop (customers' emails and ids are classified columns)."""
    data = write_dataset(tmp_path / "data", shop_tables())
    result = api11.ok(
        "profile", source=str(data), dataset=True, output=str(tmp_path / "shop.shape")
    )
    return tmp_path, result


NAMES = [f"Person Number{i}" for i in range(1, 51)]


# ---- profile_show ----------------------------------------------------------------------------


def test_profile_show_gives_what_profile_gave(shop, api11):
    home, written = shop
    shown = api11.ok("profile_show", path=str(home / "shop.shape"))
    assert shown["content_id"] == written["content_id"] and shown["name"] == written["name"]
    assert shown["tables"] == written["tables"]
    assert shown["summary"] == written["summary"]
    assert shown["signed"] is False
    assert set(written) - {"path"} <= set(shown)


def test_profile_show_matches_the_clis_json_when_values_are_asked_for(shop, api11, capsys):
    home, _ = shop
    code, _, _ = cli(
        capsys,
        "profile",
        home / "data",
        "--dataset",
        "-o",
        home / "c.shape",
        "--json",
        home / "c.json",
    )
    assert code == 0
    shown = api11.ok("profile_show", {"include_raw_values": True}, path=str(home / "c.shape"))
    assert shown["summary"] == json.loads((home / "c.json").read_text())


def test_an_unsigned_profile_warns_and_a_signed_one_says_so(shop, api11):
    home, _ = shop
    response = api11.call("profile_show", path=str(home / "shop.shape"))
    assert [w["code"] for w in response["warnings"]] == ["artifact_not_verified"]
    from shape.artifact.signing import generate_keypair, sign_artifact

    private, _ = generate_keypair()
    sign_artifact(home / "shop.shape", private, home / "signed.shape")
    signed = api11.call("profile_show", path=str(home / "signed.shape"))
    assert signed["ok"] and signed["result"]["signed"] is True
    assert signed["result"]["content_id"] == response["result"]["content_id"]


def test_profile_show_withholds_the_values_of_classified_columns(shop, api11):
    home, _ = shop
    response = api11.call("profile_show", path=str(home / "shop.shape"))
    text = json.dumps(response)
    assert not [v for v in [*EMAILS, *NAMES] if v in text]
    email = response["result"]["summary"]["tables"]["customers"]["columns"]["email"]
    assert email["min"] is None and email["max"] is None and email["redacted"] is True
    code = response["result"]["summary"]["tables"]["orders"]["columns"]["status_code"]
    assert (code["min"], code["max"]) == (1, 5) and "redacted" not in code  # five values: kept
    raw = api11.call("profile_show", {"include_raw_values": True}, path=str(home / "shop.shape"))
    email = raw["result"]["summary"]["tables"]["customers"]["columns"]["email"]
    assert email["min"] in EMAILS and "redacted" not in email


def test_a_large_summary_goes_to_a_file_with_the_values_still_withheld(shop, api11):
    home, _ = shop
    response = api11.call("profile_show", {"max_inline_bytes": 1024}, path=str(home / "shop.shape"))
    summary = response["result"]["summary"]
    assert summary["spilled"] is True
    assert "result_in_file" in [w["code"] for w in response["warnings"]]
    on_disk = open(summary["path"]).read()
    assert not [e for e in EMAILS if e in on_disk] and not [
        e for e in EMAILS if e in json.dumps(response)
    ]


def test_profile_show_errors(shop, api11):
    home, _ = shop
    api11.fail("profile_show", "input.not_found", path=str(home / "none.shape"))
    (home / "text.shape").write_text("not a zip")
    api11.fail("profile_show", "input.invalid_schema", path=str(home / "text.shape"))
    with zipfile.ZipFile(home / "empty.shape", "w"):
        pass
    api11.fail("profile_show", "input.invalid_schema", path=str(home / "empty.shape"))
    api11.fail("profile_show", "io.read_failed", path=str(home))
    api11.fail("profile_show", "usage.missing_argument")
    # a safe profile is JSON, not a .shape artifact
    safe = home / "safe.json"
    assert main(["profile", "safe", str(home / "shop.shape"), "-o", str(safe)]) == 0
    api11.fail("profile_show", "input.invalid_schema", path=str(safe))


def test_a_profile_of_a_newer_format_version_is_refused_not_misread(shop, api11):
    from shape.artifact.io import read_artifact, write_artifact

    home, _ = shop
    manifest, parts = read_artifact(str(home / "shop.shape"))
    manifest = {**manifest, "format_version": 99}
    write_artifact(str(home / "newer.shape"), manifest, parts)
    error = api11.fail(
        "profile_show", "input.unsupported_format_version", path=str(home / "newer.shape")
    )
    assert "99" in error["message"] and error["hint"]


# ---- contract_validate -----------------------------------------------------------------------

GOOD = {
    "row_count": {"min": 1, "max": 100},
    "columns": {
        "id": {"dtype": "integer", "unique": True},
        "status": {"allowed_values": ["new", "paid"], "max_null_rate": 0.1},
        "flag": {"min_true_rate": 0.1, "max_true_rate": 0.9},
        "note": {"no_placeholder": {"max_share": 0.01, "allow": ["N/A"]}},
    },
    "required_columns": ["id"],
    "allow_extra_columns": True,
    "fd": [{"determinant": "zip", "dependent": "city", "min_confidence": 0.99}],
    "max_implausible_rate": 0.05,
}


def test_a_good_contract_is_valid_from_text_and_from_a_path(tmp_path, api11):
    (tmp_path / "c.json").write_text(json.dumps(GOOD))
    for args in ({"text": json.dumps(GOOD)}, {"path": str(tmp_path / "c.json")}):
        result = api11.ok("contract_validate", **args)
        assert result["valid"] is True and result["errors"] == []


def test_the_empty_contract_is_valid(api11):
    assert api11.ok("contract_validate", text="{}")["valid"] is True


BAD = [
    ({"rows": 1}, "", "unknown contract keys"),
    ({"row_count": {"minimum": 1}}, "row_count", "row_count accepts only"),
    ({"row_count": 5}, "row_count", "row_count accepts only"),
    ({"columns": []}, "columns", "'columns' must be an object"),
    ({"columns": {"a": 1}}, "columns.a", "rules for column 'a' must be an object"),
    ({"columns": {"a": {"colour": 1}}}, "columns.a", "unknown rules for column 'a'"),
    ({"columns": {"a": {"allowed_values": "x"}}}, "columns.a", "allowed_values for column 'a'"),
    ({"columns": {"a": {"min_true_rate": 2}}}, "columns.a", "must be a number from 0 to 1"),
    ({"required_columns": "id"}, "required_columns", "'required_columns' must be a list"),
    ({"columns": {"a": {"no_placeholder": 5}}}, "columns.a", "no_placeholder for column 'a'"),
    ({"fd": [{"determinant": "a"}]}, "fd", "a 'fd' rule needs"),
    ({"max_implausible_rate": 2}, "max_implausible_rate", "must be a number from 0 to 1"),
]


@pytest.mark.parametrize("doc, location, message", BAD)
def test_each_malformed_contract_is_reported_where_it_is_wrong(api11, doc, location, message):
    result = api11.ok("contract_validate", text=json.dumps(doc))
    assert result["valid"] is False
    assert [e["location"] for e in result["errors"]] == [location]
    assert message in result["errors"][0]["message"]


@pytest.mark.parametrize("doc, location, message", BAD)
def test_the_rules_are_the_ones_shape_check_applies(
    shop, api11, tmp_path, capsys, doc, location, message
):
    home, _ = shop
    (tmp_path / "c.json").write_text(json.dumps(doc))
    code, _, err = cli(capsys, "check", home / "shop.shape", tmp_path / "c.json")
    assert code == 2 and message.split("'")[0].strip() in err
    assert api11.ok("contract_validate", path=str(tmp_path / "c.json"))["valid"] is False
    (tmp_path / "ok.json").write_text(json.dumps(GOOD))


def test_a_valid_contract_is_one_shape_check_accepts(shop, api11, tmp_path, capsys):
    home, _ = shop
    contract = {"row_count": {"min": 1}, "columns": {"customer_id": {"dtype": "integer"}}}
    (tmp_path / "c.json").write_text(json.dumps(contract))
    assert api11.ok("contract_validate", path=str(tmp_path / "c.json"))["valid"] is True
    # `check` takes one table's profile: use a one-table profile
    one = write_dataset(tmp_path / "one", {"customers": shop_tables()["customers"]})
    api11.ok("profile", source=str(one / "customers.csv"), output=str(tmp_path / "one.shape"))
    code, _, _ = cli(capsys, "check", tmp_path / "one.shape", tmp_path / "c.json")
    assert code in (0, 1)


def test_every_problem_is_listed_not_just_the_first(api11):
    doc = {
        "rows": 1,
        "row_count": {"x": 1},
        "columns": {"a": {"colour": 1}, "b": {"unique": 1, "min_true_rate": 3}},
    }
    result = api11.ok("contract_validate", text=json.dumps(doc))
    assert sorted(e["location"] for e in result["errors"]) == [
        "",
        "columns.a",
        "columns.b",
        "row_count",
    ]


def test_contract_validate_for_text_that_is_not_a_contract(api11):
    for text in ("", "{", "[]", "3", "null", '"x"', '{"columns": 1}'):
        result = api11.ok("contract_validate", text=text)
        assert result["valid"] is False and result["errors"], text
    assert "not valid JSON" in api11.ok("contract_validate", text="{")["errors"][0]["message"]


def test_contract_validate_errors(tmp_path, api11):
    api11.fail("contract_validate", "usage.invalid_argument")
    api11.fail("contract_validate", "usage.invalid_argument", text="{}", path="x.json")
    api11.fail("contract_validate", "input.not_found", path=str(tmp_path / "none.json"))
    api11.fail("contract_validate", "io.read_failed", path=str(tmp_path))
    (tmp_path / "bin.json").write_bytes(b"\xff\xfe")
    api11.fail("contract_validate", "input.invalid_schema", path=str(tmp_path / "bin.json"))


def test_the_1_0_validate_command_is_not_changed_by_it(tmp_path, api):
    (tmp_path / "c.json").write_text(json.dumps(GOOD))
    error = api.fail("validate", "input.invalid_schema", schema_path=str(tmp_path / "c.json"))
    assert "neither a Shape generation schema" in error["message"]
    (tmp_path / "d.json").write_text(json.dumps({"name": "x", "fields": []}))
    assert api.ok("validate", schema_path=str(tmp_path / "d.json"))["kind"] == "contract"


# ---- safe_scan -------------------------------------------------------------------------------


@pytest.fixture
def safe_file(shop):
    home, _ = shop
    path = home / "safe.json"
    assert main(["profile", "safe", str(home / "shop.shape"), "-o", str(path)]) == 0
    return path


def test_a_safe_profile_is_clean(safe_file, api11, capsys):
    assert api11.ok("safe_scan", path=str(safe_file)) == {"clean": True, "findings": []}
    code, out, _ = cli(capsys, "profile", "validate", "--safe", safe_file, "--json")
    assert code == 0 and json.loads(out)["clean"] is True
    assert api11.ok("safe_scan", text=safe_file.read_text()) == {"clean": True, "findings": []}


def test_a_full_profile_is_flagged_and_its_values_are_not_in_the_response(shop, api11, capsys):
    home, _ = shop
    response = api11.call("safe_scan", path=str(home / "shop.shape"))
    assert response["ok"] and response["result"]["clean"] is False
    findings = response["result"]["findings"]
    assert "not-safe-profile" in {f["rule"] for f in findings}
    text = json.dumps(response)
    assert not [e for e in EMAILS if e in text]
    code, out, _ = cli(capsys, "profile", "validate", "--safe", home / "shop.shape", "--json")
    expected = json.loads(out)
    assert code == 1 and expected["clean"] is False
    assert [f["rule"] for f in findings] == [f["rule"] for f in expected["findings"]]
    # the CLI's own report does hold values the bridge withholds
    assert [e for e in EMAILS if e in out]


def test_the_values_come_back_only_when_asked_and_then_match_the_cli(shop, api11, capsys):
    home, _ = shop
    raw = api11.ok("safe_scan", {"include_raw_values": True}, path=str(home / "shop.shape"))
    code, out, _ = cli(capsys, "profile", "validate", "--safe", home / "shop.shape", "--json")
    expected = json.loads(out)["findings"]
    assert [(f["rule"], f["pointer"], f["message"]) for f in raw["findings"]] == [
        (f["rule"], f["path"], f["detail"]) for f in expected
    ]


LEAKS = {
    "schema_version": 1,
    "tables": {
        "t": {
            "row_count": 10,
            "columns": {
                "mail": {"example": "alice.smith@example.com"},
                "people": {"names": ["Zed Zimmer", "Yan Yu", "Xi Xu", "Wu Wang"]},
                "amount": {"min": 123456, "max": 987654},
                "ssn": {"v": "123-45-6789"},
                "phone": {"v": "call +1 415 555 0132 now"},
                "ip": {"v": "10.20.30.40"},
                "iban": {"v": "DE89370400440532013000"},
            },
        },
        "empty": {"row_count": 0},
        "odd": {"row_count": "12"},
    },
}
RAW = [
    "alice.smith@example.com", "Zed Zimmer", "Yan Yu", "Xi Xu", "Wu Wang", "123456", "987654",
    "123-45-6789", "415 555 0132", "10.20.30.40", "DE89370400440532013000", "12",
]  # fmt: skip


def test_no_message_holds_the_value_it_found(api11):
    response = api11.call("safe_scan", text=json.dumps(LEAKS))
    assert response["ok"]
    findings = response["result"]["findings"]
    rules = {f["rule"] for f in findings}
    assert {"pii-regex", "raw-string-list", "extreme-pair", "row-count-missing"} <= rules
    text = json.dumps(response)
    for value in RAW:
        assert value not in text, value
    assert all(f["message"] and f["pointer"].startswith("$") for f in findings)
    labels = {f["message"] for f in findings if f["rule"] == "pii-regex"}
    assert {
        "a value matches the email pattern",
        "a value matches the ssn pattern",
        "a value matches the ip pattern",
        "a value matches the iban pattern",
        "a value matches the phone pattern",
    } <= labels


def test_with_raw_values_the_messages_hold_them(api11):
    response = api11.call("safe_scan", {"include_raw_values": True}, text=json.dumps(LEAKS))
    text = json.dumps(response)
    for value in ("alice.smith@example.com", "123-45-6789", "Zed Zimmer", "123456"):
        assert value in text


def test_a_pointer_that_is_itself_a_value_is_withheld(api11):
    doc = {
        "schema_version": 1,
        "tables": {
            "t": {"row_count": 3, "columns": {"alice@example.com": {"v": "bob@example.com"}}}
        },
    }
    response = api11.call("safe_scan", text=json.dumps(doc))
    text = json.dumps(response)
    assert "alice@example.com" not in text and "bob@example.com" not in text
    assert "<redacted>" in text


def test_scan_findings_for_text_that_is_not_a_document(api11):
    for text, rule in (("{", "malformed"), ("", "malformed"), ("[]", "not-safe-profile")):
        result = api11.ok("safe_scan", text=text)
        assert result["clean"] is False and result["findings"][0]["rule"] == rule, text
    deep = "[" * 200 + "]" * 200
    assert api11.ok("safe_scan", text=deep)["findings"][0]["rule"] == "malformed"
    unsafe = {"schema_version": 1, "unsafe": True, "tables": {}}
    assert "unsafe-stamp" in [
        f["rule"] for f in api11.ok("safe_scan", text=json.dumps(unsafe))["findings"]
    ]


def test_scan_of_a_file_that_is_not_json_is_a_finding_and_a_missing_file_is_an_error(
    tmp_path, api11
):
    (tmp_path / "x.json").write_text("{nope")
    assert (
        api11.ok("safe_scan", path=str(tmp_path / "x.json"))["findings"][0]["rule"] == "malformed"
    )
    api11.fail("safe_scan", "input.not_found", path=str(tmp_path / "none.json"))
    api11.fail("safe_scan", "usage.invalid_argument")
    api11.fail("safe_scan", "usage.invalid_argument", text="{}", path="x")


def test_a_scan_writes_nothing(safe_file, api11):
    before = sorted(p.name for p in safe_file.parent.iterdir())
    api11.ok("safe_scan", path=str(safe_file))
    assert sorted(p.name for p in safe_file.parent.iterdir()) == before

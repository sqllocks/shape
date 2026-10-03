"""W7-04 item 3: ``proposals_propose``, ``proposals_list`` and ``proposals_decide``."""

from __future__ import annotations

import json
import time

import pytest
from data_1_1 import EMAILS, mail_tables, shop_tables, write_dataset

from shape.cli.main import main

CUST = "relationship:orders.customer_id->customers.customer_id"
PII = "pii:customers.email"


@pytest.fixture
def shop(tmp_path, api11):
    data = write_dataset(tmp_path / "data", shop_tables())
    prof = tmp_path / "shop.shape"
    api11.ok("profile", source=str(data), dataset=True, output=str(prof))
    return tmp_path


@pytest.fixture
def mail(tmp_path, api11):
    data = write_dataset(tmp_path / "mail", mail_tables())
    prof = tmp_path / "mail.shape"
    api11.ok("profile", source=str(data), dataset=True, output=str(prof))
    return tmp_path


def cli(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out


def normal(text: str) -> dict:
    """A decision file with the time stamps taken out (two runs a second apart differ there)."""
    doc = json.loads(text)
    for p in doc["proposals"]:
        p["proposed_at"] = "T"
    for d in doc["decisions"]:
        d["at"] = "T"
    return doc


# ---- propose ----------------------------------------------------------------------------------


def test_propose_writes_the_decision_file_the_cli_writes(shop, api11, capsys):
    bridge_file, cli_file = shop / "bridge.json", shop / "cli.json"
    result = api11.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(bridge_file),
        data=[str(shop / "data")],
    )
    code, out = cli(
        capsys,
        "proposals",
        "propose",
        str(shop / "shop.shape"),
        "--data",
        str(shop / "data"),
        "-d",
        str(cli_file),
    )
    assert code == 0
    assert normal(bridge_file.read_text()) == normal(cli_file.read_text())
    expected = json.loads(out)
    assert result["decisions"] == str(bridge_file) and expected["written"] == str(cli_file)
    for key in ("proposals", "added", "updated", "withdrawn", "skipped_rejected", "auto_accepted"):
        assert result[key] == expected[key], key
    assert result["added"] == len(result["proposal_ids"]) > 0 and result["unchanged"] == 0
    assert CUST in result["proposal_ids"] and PII in result["proposal_ids"]
    assert result["skipped"] == [] and result["updated"] == 0


def test_proposing_again_changes_nothing_and_the_ids_are_the_same(shop, api11):
    args = {
        "profile": str(shop / "shop.shape"),
        "decisions": str(shop / "d.json"),
        "data": [str(shop / "data")],
    }
    first = api11.ok("proposals_propose", **args)
    before = (shop / "d.json").read_text()
    second = api11.ok("proposals_propose", **args)
    assert (shop / "d.json").read_text() == before
    assert second["added"] == 0 and second["unchanged"] == len(first["proposal_ids"])
    assert second["proposal_ids"] == first["proposal_ids"]


def test_propose_without_data_proposes_from_the_profile_alone(shop, api11):
    result = api11.ok(
        "proposals_propose", profile=str(shop / "shop.shape"), decisions=str(shop / "d.json")
    )
    assert CUST in result["proposal_ids"]
    with_data = api11.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "e.json"),
        data=[f"customers={shop / 'data' / 'customers.csv'}", f"orders={shop / 'data/orders.csv'}"],
    )
    assert CUST in with_data["proposal_ids"]


def test_kinds_and_min_confidence_narrow_the_run(shop, api11):
    result = api11.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "d.json"),
        data=[str(shop / "data")],
        kinds=["pii"],
        min_confidence=0.0,
    )
    assert result["proposal_ids"] and all(i.startswith("pii:") for i in result["proposal_ids"])
    none = api11.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "high.json"),
        min_confidence=1,
    )
    assert none["added"] == 0 and none["proposal_ids"] == []


def test_nothing_is_accepted_unless_auto_accept_is_given(shop, api11):
    args = {"profile": str(shop / "shop.shape"), "data": [str(shop / "data")]}
    api11.ok("proposals_propose", decisions=str(shop / "plain.json"), **args)
    rows = api11.ok("proposals_list", decisions=str(shop / "plain.json"))["proposals"]
    assert all(r["decision"]["status"] == "pending" for r in rows)
    result = api11.ok(
        "proposals_propose", decisions=str(shop / "auto.json"), auto_accept=0.9, **args
    )
    assert result["auto_accepted"]
    rows = api11.ok("proposals_list", decisions=str(shop / "auto.json"), status="accepted")
    assert {r["id"] for r in rows["proposals"]} == set(result["auto_accepted"])
    assert {r["decision"]["actor"] for r in rows["proposals"]} == {"auto-accept"}


def test_a_rejected_proposal_is_not_proposed_again(shop, api11):
    args = {
        "profile": str(shop / "shop.shape"),
        "decisions": str(shop / "d.json"),
        "data": [str(shop / "data")],
    }
    api11.ok("proposals_propose", **args)
    api11.ok("proposals_decide", decisions=args["decisions"], proposal=PII, verb="reject")
    again = api11.ok("proposals_propose", **args)
    assert again["skipped"] == [PII] and again["unchanged"] == len(again["proposal_ids"]) - 1
    # auto-accept never overrides a decision
    api11.ok("proposals_propose", auto_accept=0, **args)
    rows = api11.ok("proposals_list", decisions=args["decisions"], status="rejected")
    assert [r["id"] for r in rows["proposals"]] == [PII]


def test_propose_as_a_job(shop, api11):
    started = api11.ok(
        "proposals_propose",
        {"async": True},
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "d.json"),
        data=[str(shop / "data")],
    )
    job_id = started["job_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = api11.ok("job_status", job_id=job_id)
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded" and CUST in job["result"]["proposal_ids"]


def test_a_bad_propose_request_as_a_job_leaves_no_failed_job(shop, api11):
    api11.fail(
        "proposals_propose",
        "input.not_found",
        {"async": True},
        profile=str(shop / "missing.shape"),
        decisions=str(shop / "d.json"),
    )
    assert api11.ok("job_list")["jobs"] == []


@pytest.mark.parametrize(
    "args, code",
    [
        ({"profile": "MISSING"}, "input.not_found"),
        ({"data": ["MISSING"]}, "input.not_found"),
        ({"data": ["customers=MISSING"]}, "input.not_found"),
        ({"data": ["DATA", "customers=X"]}, "usage.invalid_argument"),
        ({"data": ["a", "b"]}, "usage.invalid_argument"),
        ({"kinds": ["relationship", "mystery"]}, "usage.invalid_argument"),
        ({"min_confidence": 1.01}, "usage.invalid_argument"),
        ({"min_confidence": -0.1}, "usage.invalid_argument"),
        ({"auto_accept": 2}, "usage.invalid_argument"),
        ({"min_confidence": "high"}, "usage.invalid_argument"),
    ],
)
def test_propose_refuses_bad_requests(shop, api11, args, code):
    request = {
        "profile": str(shop / "shop.shape"),
        "decisions": str(shop / "d.json"),
        "data": [str(shop / "data")],
    }
    for key, value in args.items():
        if key == "data":
            value = [
                v.replace("DATA", str(shop / "data")).replace("MISSING", str(shop / "no"))
                for v in value
            ]
            if value == ["a", "b"]:
                value = [str(shop / "data"), str(shop / "data")]
        elif value == "MISSING":
            value = str(shop / "no.shape")
        request[key] = value
    api11.fail("proposals_propose", code, **request)
    assert not (shop / "d.json").exists()  # a refused request writes nothing


def test_propose_names_a_profile_that_is_not_a_profile(shop, api11):
    (shop / "junk.shape").write_text("not a profile")
    api11.fail(
        "proposals_propose",
        "input.invalid_schema",
        profile=str(shop / "junk.shape"),
        decisions=str(shop / "d.json"),
    )


def test_propose_reads_a_malformed_or_newer_decision_file_and_does_not_touch_it(shop, api11):
    args = {"profile": str(shop / "shop.shape"), "decisions": str(shop / "d.json")}
    (shop / "d.json").write_text("{not json")
    api11.fail("proposals_propose", "input.invalid_schema", **args)
    assert (shop / "d.json").read_text() == "{not json"
    newer = {"format": "shape-decisions", "version": 2, "proposals": [], "decisions": []}
    (shop / "d.json").write_text(json.dumps(newer))
    error = api11.fail("proposals_propose", "input.unsupported_format_version", **args)
    assert "version 2" in error["message"] and json.loads((shop / "d.json").read_text()) == newer


def test_propose_cannot_write_into_a_missing_folder(shop, api11):
    api11.fail(
        "proposals_propose",
        "io.write_failed",
        profile=str(shop / "shop.shape"),
        decisions=str(shop / "no" / "such" / "d.json"),
    )


# ---- list -------------------------------------------------------------------------------------


@pytest.fixture
def decided(shop, api11):
    path = str(shop / "d.json")
    api11.ok(
        "proposals_propose",
        profile=str(shop / "shop.shape"),
        decisions=path,
        data=[str(shop / "data")],
    )
    api11.ok(
        "proposals_decide", decisions=path, proposal=CUST, verb="accept", actor="ana", note="ok"
    )
    api11.ok("proposals_decide", decisions=path, proposal=PII, verb="defer", actor="bo")
    return path


def test_list_matches_the_clis_json_and_adds_the_decision(decided, api11, capsys):
    result = api11.ok("proposals_list", decisions=decided)
    code, out = cli(capsys, "proposals", "list", "-d", decided, "--json")
    rows = json.loads(out)
    assert [r["id"] for r in result["proposals"]] == [r["id"] for r in rows]
    assert result["count"] == len(rows)
    for mine, theirs in zip(result["proposals"], rows, strict=True):
        assert mine["confidence"] == theirs["confidence"] and mine["claim"] == theirs["claim"]
        assert mine["kind"] == theirs["kind"] and mine["decision"]["status"] == theirs["status"]
        assert mine["subject"] == theirs["subject"]
    by_id = {r["id"]: r for r in result["proposals"]}
    assert by_id[CUST]["decision"] == {
        "status": "accepted",
        "actor": "ana",
        "note": "ok",
        "decided_at": by_id[CUST]["decision"]["decided_at"],
    }
    assert by_id[CUST]["decision"]["decided_at"].endswith("Z")
    assert by_id[PII]["decision"]["status"] == "deferred"
    pending = [r for r in result["proposals"] if r["decision"]["status"] == "pending"]
    assert pending and all(
        r["decision"]["actor"] is None and r["decision"]["decided_at"] is None for r in pending
    )
    confidences = [r["confidence"] for r in result["proposals"]]
    assert confidences == sorted(confidences, reverse=True)


def test_list_filters(decided, api11):
    def ids(**kw):
        return {r["id"] for r in api11.ok("proposals_list", decisions=decided, **kw)["proposals"]}

    assert ids(status="accepted") == {CUST}
    assert ids(status="deferred") == {PII}
    assert ids(kind="relationship") >= {CUST} and all(
        i.startswith("relationship:") for i in ids(kind="relationship")
    )
    assert CUST in ids(min_confidence=0.9)
    assert ids(min_confidence=0.0) == ids() and ids(min_confidence=1) <= ids()
    assert ids(status="rejected") == set()
    assert ids(status="accepted", kind="pii") == set()


@pytest.mark.parametrize(
    "args",
    [{"status": "done"}, {"kind": "mystery"}, {"min_confidence": 2}, {"min_confidence": "x"}],
)
def test_list_refuses_bad_filters(decided, api11, args):
    api11.fail("proposals_list", "usage.invalid_argument", decisions=decided, **args)


def test_list_errors_for_a_missing_malformed_or_newer_file(shop, api11):
    api11.fail("proposals_list", "input.not_found", decisions=str(shop / "none.json"))
    (shop / "bad.json").write_text("[1, 2]")
    api11.fail("proposals_list", "input.invalid_schema", decisions=str(shop / "bad.json"))
    (shop / "bad.json").write_text(json.dumps({"format": "other", "version": 1}))
    api11.fail("proposals_list", "input.invalid_schema", decisions=str(shop / "bad.json"))
    (shop / "bad.json").write_bytes(b"\xff\xfe")
    api11.fail("proposals_list", "input.invalid_schema", decisions=str(shop / "bad.json"))
    (shop / "bad.json").write_text(
        json.dumps({"format": "shape-decisions", "version": 9, "proposals": [], "decisions": []})
    )
    api11.fail(
        "proposals_list", "input.unsupported_format_version", decisions=str(shop / "bad.json")
    )
    api11.fail("proposals_list", "io.read_failed", decisions=str(shop))


def test_a_large_list_goes_to_a_file(decided, api11):
    response = api11.call("proposals_list", {"max_inline_bytes": 1024}, decisions=decided)
    result = response["result"]
    assert result["proposals"]["spilled"] is True
    assert "result_in_file" in [w["code"] for w in response["warnings"]]
    spilled = json.loads(open(result["proposals"]["path"]).read())
    assert [r["id"] for r in spilled] == [
        r["id"] for r in api11.ok("proposals_list", decisions=decided)["proposals"]
    ]


# ---- decide -----------------------------------------------------------------------------------


def test_decide_matches_the_cli(shop, api11, capsys):
    cli_file, bridge_file = str(shop / "c.json"), str(shop / "b.json")
    for target in (cli_file, bridge_file):
        api11.ok(
            "proposals_propose",
            profile=str(shop / "shop.shape"),
            decisions=target,
            data=[str(shop / "data")],
        )
    result = api11.ok(
        "proposals_decide",
        decisions=bridge_file,
        proposal=CUST,
        verb="accept",
        actor="ana",
        note="orders belong to customers",
    )
    code, out = cli(
        capsys, "proposals", "decide", "-d", cli_file, CUST, "accept", "--actor", "ana",
        "--note", "orders belong to customers",
    )  # fmt: skip
    expected = json.loads(out)
    assert code == 0
    assert result["id"] == CUST and result["decision"]["status"] == expected["status"] == "accepted"
    assert result["decision"]["actor"] == expected["actor"] == "ana"
    assert result["decision"]["note"] == expected["note"]
    assert result["decision"]["decided_at"].endswith("Z")
    assert normal(open(bridge_file).read()) == normal(open(cli_file).read())
    assert result["kind"] == "relationship" and result["claim"]["child"] == "orders"


@pytest.mark.parametrize(
    "verb, status", [("accept", "accepted"), ("reject", "rejected"), ("defer", "deferred")]
)
def test_decide_records_each_verb_and_a_second_decision_replaces_the_first(
    decided, api11, verb, status
):
    result = api11.ok("proposals_decide", decisions=decided, proposal=PII, verb=verb, actor="cy")
    assert result["decision"]["status"] == status
    rows = api11.ok("proposals_list", decisions=decided, kind="pii")["proposals"]
    assert [r["decision"]["status"] for r in rows if r["id"] == PII] == [status]
    file = json.loads(open(decided).read())
    assert file["format"] == "shape-decisions" and file["version"] == 1
    assert len([d for d in file["decisions"] if d["proposal"] == PII]) == 1


def test_the_actor_defaults_to_shape_actor_then_the_login_name(decided, api11, monkeypatch):
    monkeypatch.setenv("SHAPE_ACTOR", "from-env")
    r = api11.ok("proposals_decide", decisions=decided, proposal=PII, verb="accept")
    assert r["decision"]["actor"] == "from-env" and r["decision"]["note"] == ""
    r = api11.ok("proposals_decide", decisions=decided, proposal=PII, verb="accept", actor="given")
    assert r["decision"]["actor"] == "given"
    monkeypatch.delenv("SHAPE_ACTOR")
    monkeypatch.setattr("getpass.getuser", lambda: "login-name")
    r = api11.ok("proposals_decide", decisions=decided, proposal=PII, verb="accept")
    assert r["decision"]["actor"] == "login-name"


def test_decide_errors(decided, api11, shop):
    before = open(decided).read()
    error = api11.fail(
        "proposals_decide",
        "input.unknown_proposal",
        decisions=decided,
        proposal="pii:nope.x",
        verb="accept",
        actor="a",
    )
    assert "pii:nope.x" in error["message"] and error["hint"]
    api11.fail(
        "proposals_decide", "usage.invalid_argument", decisions=decided, proposal=PII, verb="maybe"
    )
    api11.fail(
        "usage.missing_argument" and "proposals_decide",
        "usage.missing_argument",
        decisions=decided,
        verb="accept",
    )
    api11.fail(
        "proposals_decide",
        "input.invalid_value",
        decisions=decided,
        proposal=PII,
        verb="accept",
        actor="   ",
    )
    api11.fail(
        "proposals_decide",
        "input.not_found",
        decisions=str(shop / "none.json"),
        proposal=PII,
        verb="accept",
        actor="a",
    )
    assert open(decided).read() == before  # a refused decision changes nothing


def test_the_kinds_are_the_engines():
    from shape.bridge.handlers import proposals as module
    from shape.proposals import KINDS

    assert module._KINDS == KINDS


# ---- safe by default --------------------------------------------------------------------------

MAIL = "relationship:messages.email->users.email"
LOW, HIGH = "person10@example.com", "person9@example.com"  # the text order of EMAILS' ends


def all_text(value) -> str:
    return json.dumps(value)


def test_list_withholds_the_values_in_evidence_of_a_classified_column(mail, api11):
    decisions = str(mail / "d.json")
    api11.ok(
        "proposals_propose",
        profile=str(mail / "mail.shape"),
        decisions=decisions,
        data=[str(mail / "mail")],
    )
    assert MAIL in (open(decisions).read())
    # the file itself holds the values (the smallest and largest email): that is the point
    assert LOW in open(decisions).read() and HIGH in open(decisions).read()
    response = api11.call("proposals_list", decisions=decisions)
    text = all_text(response)
    for value in EMAILS:
        assert value not in text, value
    row = {r["id"]: r for r in response["result"]["proposals"]}[MAIL]
    assert row["redacted"] is True
    assert row["evidence"]["range"]["child"] is None and row["evidence"]["range"]["parent"] is None
    assert row["evidence"]["range"]["within"] is True  # what is not a value stays
    assert row["evidence"]["containment"]["fraction"] == 1.0
    assert row["evidence"]["name"]["score"] > 0


def test_list_returns_the_values_only_when_asked(mail, api11):
    decisions = str(mail / "d.json")
    api11.ok("proposals_propose", profile=str(mail / "mail.shape"), decisions=decisions)
    response = api11.call("proposals_list", {"include_raw_values": True}, decisions=decisions)
    row = {r["id"]: r for r in response["result"]["proposals"]}[MAIL]
    assert row["evidence"]["range"]["child"] == [LOW, HIGH]
    assert row["evidence"]["range"]["parent"] == [LOW, HIGH]
    assert "redacted" not in row


def test_decide_and_propose_do_not_leak_values_either(mail, api11):
    decisions = str(mail / "d.json")
    propose = api11.call(
        "proposals_propose",
        profile=str(mail / "mail.shape"),
        decisions=decisions,
        data=[str(mail / "mail")],
    )
    decide = api11.call(
        "proposals_decide", decisions=decisions, proposal=MAIL, verb="accept", actor="a"
    )
    for response in (propose, decide):
        assert response["ok"]
        text = all_text(response)
        assert not [v for v in EMAILS if v in text]
    assert decide["result"]["redacted"] is True
    raw = api11.call(
        "proposals_decide",
        {"include_raw_values": True},
        decisions=decisions,
        proposal=MAIL,
        verb="accept",
        actor="a",
    )
    assert "redacted" not in raw["result"]


def test_a_spilled_list_is_redacted_too(mail, api11):
    decisions = str(mail / "d.json")
    api11.ok("proposals_propose", profile=str(mail / "mail.shape"), decisions=decisions)
    response = api11.call("proposals_list", {"max_inline_bytes": 1024}, decisions=decisions)
    spill = response["result"]["proposals"]
    if spill.get("spilled"):
        assert not [v for v in EMAILS if v in open(spill["path"]).read()]
    assert not [v for v in EMAILS if v in all_text(response)]


def test_redact_evidence_denies_lists_of_values_wherever_they_sit():
    from shape.bridge.handlers.proposals import redact_evidence

    evidence = {
        "samples": ["a@b.com", "c@d.org"],
        "nested": {"examples": [1, 2, 3], "count": 3, "ok": True},
        "range": {"within": True, "child": [1, 9], "parent": ["x", "z"]},
        "names": [{"a": 1}],
        "empty": [],
    }
    redacted, withheld = redact_evidence(evidence)
    assert withheld is True
    assert redacted["samples"] is None and redacted["nested"]["examples"] is None
    assert redacted["range"] == {"within": True, "child": None, "parent": None}
    assert redacted["nested"]["count"] == 3 and redacted["names"] == [{"a": 1}]
    assert redacted["empty"] == []
    assert redact_evidence({"name": {"score": 1.0}}) == ({"name": {"score": 1.0}}, False)
    assert redact_evidence({"range": {"within": None}}) == ({"range": {"within": None}}, False)

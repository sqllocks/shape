"""Bridge 1.2 keeps the 1.1 promise (W7-05, item 1).

``docs/bridge/schema/1.1/`` and ``docs/bridge/vectors/1.1/`` are the 1.1 contract, frozen: this file
replays every 1.1 vector against the 1.2 bridge (every field equal except the response's own
``api_version``) and holds the 1.2 schemas to the 1.1 ones, next to the 1.0 replay of
``test_compat_1_0.py``. The deliberate negative tests at the end show that each check fails when
1.1 is broken.
"""

from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeFabric
from test_compat_1_0 import differences, schema_losses, substitute, without_version

from shape.bridge.core import Bridge
from shape.bridge.protocol import API_VERSION, ERROR_CODES, WARNING_CODES
from shape.bridge.registry import COMMANDS
from shape.bridge.schemas import all_schemas

DOCS = Path(__file__).resolve().parents[2] / "docs" / "bridge"
FROZEN_SCHEMAS = DOCS / "schema" / "1.1"
FROZEN_VECTORS = DOCS / "vectors" / "1.1"

#: The warning codes of 1.1, frozen here: ``project_source_not_selected`` came with 1.1.
WARNING_CODES_1_1 = (
    "api_version_assumed",
    "newer_minor_version",
    "artifact_not_verified",
    "empty_table",
    "profile_file_holds_values",
    "domain_load_failed",
    "output_dir_ignored",
    "result_in_file",
    "project_source_not_selected",
)


def frozen_vector_files() -> list[Path]:
    return sorted(FROZEN_VECTORS.glob("*.json"))


def frozen_index() -> dict[str, Any]:
    return json.loads((FROZEN_SCHEMAS / "index.json").read_text())


def frozen(rel: str) -> dict[str, Any]:
    return json.loads((FROZEN_SCHEMAS / rel).read_text())


# ---- the frozen files are the 1.1 contract -------------------------------------------------


def test_the_frozen_1_1_files_are_there_and_say_1_1():
    index = frozen_index()
    assert index["api_version"] == "1.1" and len(index["commands"]) == 35
    assert len(frozen_vector_files()) == 35
    for path in frozen_vector_files():
        doc = json.loads(path.read_text())
        assert doc["command"] == path.stem
        assert all(c["response"]["api_version"] == "1.1" for c in doc["cases"])
        # a 1.0 command's cases declare 1.0 and a 1.1 command's declare 1.1: never 1.2
        assert all(c["request"]["api_version"] in ("1.0", "1.1") for c in doc["cases"])
    for path in FROZEN_SCHEMAS.rglob("*.json"):
        assert json.loads(path.read_text()).get("x-api-version", "1.1") == "1.1", path


def test_the_frozen_schemas_name_no_1_2_command():
    current = all_schemas()["index.json"]["commands"]
    assert set(frozen_index()["commands"]) < set(current)
    assert not [n for n in frozen_index()["commands"] if current[n]["since"] == "1.2"]


# ---- every 1.1 vector, replayed against the 1.2 bridge ---------------------------------------


@pytest.mark.parametrize("path", frozen_vector_files(), ids=lambda p: p.stem)
def test_every_1_1_vector_gets_the_recorded_response_from_the_1_2_bridge(
    path, tmp_path, monkeypatch
):
    doc = json.loads(path.read_text())
    directory = tmp_path / "work"
    shutil.copytree(FROZEN_VECTORS / "fixtures", directory, dirs_exist_ok=True)
    jobs = directory / "jobs"
    (jobs / "bridge").mkdir(parents=True)
    for job_id, record in (doc.get("jobs") or {}).items():
        (jobs / "bridge" / f"{job_id}.json").write_text(json.dumps(record))
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor")
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    bridge = Bridge(jobs)
    for setup in doc.get("setup", []):
        assert bridge.handle(substitute(setup, str(directory)))["ok"]
    for one in doc["cases"]:
        if one.get("needs") == "fabric":
            monkeypatch.setattr("shape.scale.http.urllib_transport", FakeFabric())
        actual = bridge.handle(substitute(one["request"], str(directory)))
        expected = substitute(one["response"], str(directory))
        assert actual["api_version"] == API_VERSION == "1.2"  # the one field that differs
        assert differences(without_version(expected), without_version(actual)) == [], (
            path.stem,
            one["name"],
        )
    deadline = time.time() + 60
    while time.time() < deadline and any(j["status"] == "running" for j in bridge.jobs.list()):
        time.sleep(0.05)


# ---- the 1.2 schemas are a superset of the 1.1 schemas ------------------------------------------


def contract_losses(current: dict[str, dict[str, Any]]) -> list[str]:
    """Everything 1.1 promised that ``current`` (the generated 1.2 schemas) no longer does."""
    losses: list[str] = []
    old_index, new_index = frozen_index(), current["index.json"]
    for name, entry in old_index["commands"].items():
        if name not in new_index["commands"]:
            losses.append(f"command {name}: removed")
            continue
        new_entry = new_index["commands"][name]
        for key in ("job", "cancellable", "pending", "since", "effects"):
            if new_entry[key] != entry[key]:
                losses.append(f"command {name}: {key} changed")
        old_request = frozen(entry["request"])["properties"]["args"]
        new_request = current[entry["request"]]["properties"]["args"]
        losses.extend(schema_losses(old_request, new_request, f"{name}.args", request=True))
        for arg, since in entry["args"].items():
            if new_entry["args"].get(arg) != since:
                losses.append(f"{name}.{arg}: no longer since {since}")
        losses.extend(
            schema_losses(
                frozen(entry["result"]), current[entry["result"]], f"{name}.result", request=False
            )
        )
    for code, meaning in old_index["error_codes"].items():
        if new_index["error_codes"].get(code) != meaning:
            losses.append(f"error code {code}: removed or its meaning changed")
    for code, meaning in old_index["warning_codes"].items():
        if new_index["warning_codes"].get(code) != meaning:
            losses.append(f"warning code {code}: removed or its meaning changed")
    return losses


def test_the_1_2_schemas_keep_everything_1_1_promised():
    assert contract_losses(all_schemas()) == []


def test_every_1_1_error_code_and_warning_code_is_still_there_with_its_meaning():
    for code, meaning in frozen_index()["error_codes"].items():
        assert ERROR_CODES[code] == meaning
    assert set(WARNING_CODES_1_1) == set(frozen_index()["warning_codes"])
    assert set(WARNING_CODES_1_1) <= set(WARNING_CODES)


def test_the_1_1_commands_are_published_as_since_1_0_or_1_1_and_the_new_ones_as_1_2():
    index = all_schemas()["index.json"]["commands"]
    old = frozen_index()["commands"]
    assert {n: c["since"] for n, c in index.items() if n in old} == {
        n: c["since"] for n, c in old.items()
    }
    new = sorted(set(index) - set(old))
    assert new and all(index[n]["since"] == "1.2" for n in new)
    for name in new:
        assert set(index[name]["args"].values()) == {"1.2"}


def test_what_1_2_adds_to_a_1_1_command_is_marked_and_nothing_else_changed():
    """The only 1.2 addition to an old command is an enumeration value (``rule`` in ``kinds``,
    ``stale`` in a status): the argument itself stays since 1.1 and the value says since 1.2."""
    schemas = all_schemas()
    request = schemas["commands/proposals_propose.request.schema.json"]
    kinds = request["properties"]["args"]["properties"]["kinds"]
    assert kinds["x-since"] == "1.1" and "rule" in kinds["items"]["enum"]
    assert kinds["items"]["x-enum-since"] == {"rule": "1.2"}
    old_kinds = frozen("commands/proposals_propose.request.schema.json")["properties"]["args"][
        "properties"
    ]["kinds"]
    assert "rule" not in old_kinds["items"]["enum"]
    for name, entry in frozen_index()["commands"].items():
        now = schemas[entry["request"]]["properties"]["args"]["properties"]
        before = frozen(entry["request"])["properties"]["args"]["properties"]
        assert set(now) == set(before), f"{name} gained or lost an argument in 1.2"


# ---- jobs written by the 1.1 bridge are read by the 1.2 bridge -------------------------------------


def test_a_job_file_the_1_1_bridge_wrote_is_read_by_the_1_2_bridge(tmp_path):
    record = {
        "format": "shape-bridge-job",
        "version": 1,
        "job_id": "job-000000000011",
        "command": "proposals_propose",
        "status": "succeeded",
        "created_at": "2026-01-01T00:00:00.000+00:00",
        "updated_at": "2026-01-01T00:00:01.000+00:00",
        "request": {"args": {"profile": "p.shape", "decisions": "d.json"}, "options": {}},
        "progress": {},
        "result": {"decisions": "d.json", "proposal_ids": [], "added": 0, "unchanged": 0},
        "error": None,
        "worker": {"pid": 1},
        "external": None,
        "cancellable": False,
    }
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "job-000000000011.json").write_text(json.dumps(record))
    bridge = Bridge(tmp_path)
    for version in ("1.1", "1.2"):
        got = bridge.handle(
            {"api_version": version, "command": "job_status", "args": {"job_id": record["job_id"]}}
        )
        assert got["ok"] and got["result"]["status"] == "succeeded", got
        assert got["result"]["result"] == record["result"]
    listed = bridge.handle({"api_version": "1.2", "command": "job_list"})
    assert [j["job_id"] for j in listed["result"]["jobs"]] == ["job-000000000011"]


def test_the_job_file_format_stays_shape_bridge_job_version_1(tmp_path):
    from shape.bridge.jobs import JOB_FORMAT, JOB_VERSION

    assert (JOB_FORMAT, JOB_VERSION) == ("shape-bridge-job", 1)


# ---- a request that declares 1.1 is answered as 1.1 answers it ---------------------------------

NEW_1_2 = sorted(n for n, c in COMMANDS.items() if c.since == "1.2")


@pytest.mark.parametrize("version", ["1.0", "1.1"])
@pytest.mark.parametrize("name", NEW_1_2)
def test_a_1_2_command_is_unknown_to_an_older_request(tmp_path, name, version):
    got = Bridge(tmp_path).handle({"api_version": version, "command": name, "args": {}})
    assert not got["ok"] and got["error"]["code"] == "usage.unknown_command"
    assert f"{name} needs api_version 1.2" in got["error"]["hint"]
    assert not any(n in got["error"]["hint"].split(";")[0] for n in NEW_1_2)


def test_a_1_2_enumeration_value_is_refused_to_a_1_1_request_as_1_1_refused_it(tmp_path):
    bridge = Bridge(tmp_path)
    request = {
        "api_version": "1.1",
        "command": "proposals_propose",
        "args": {"profile": "p.shape", "decisions": "d.json", "kinds": ["rule"]},
    }
    old = bridge.handle(request)
    assert old["error"]["code"] == "usage.invalid_argument"
    assert "may only hold relationship, pii, semantic, got 'rule'" in old["error"]["message"]
    request["api_version"] = "1.2"
    new = bridge.handle(request)
    assert new["error"]["code"] != "usage.invalid_argument", new  # past the check: no profile


def test_a_1_2_status_and_kind_are_refused_to_a_1_1_request(tmp_path):
    bridge = Bridge(tmp_path)
    for args in ({"status": "stale"}, {"kind": "rule"}):
        request = {
            "api_version": "1.1",
            "command": "proposals_list",
            "args": {"decisions": "d.json", **args},
        }
        assert bridge.handle(request)["error"]["code"] == "usage.invalid_argument"
        request["api_version"] = "1.2"
        assert bridge.handle(request)["error"]["code"] == "input.not_found"


def test_format_schema_gives_a_1_1_request_the_1_1_names_and_the_decisions_version_1(tmp_path):
    bridge = Bridge(tmp_path)
    old = bridge.handle({"api_version": "1.1", "command": "format_schema", "args": {}})
    new = bridge.handle({"api_version": "1.2", "command": "format_schema", "args": {}})
    assert set(old["result"]["names"]) < set(new["result"]["names"])
    assert not {"mutation-plan", "mutation-report", "incidents", "backtest-report"} & set(
        old["result"]["names"]
    )
    got = bridge.handle(
        {"api_version": "1.1", "command": "format_schema", "args": {"name": "decisions"}}
    )["result"]
    assert got["version"] == 1
    got = bridge.handle(
        {"api_version": "1.2", "command": "format_schema", "args": {"name": "decisions"}}
    )["result"]
    assert got["version"] == 2
    refused = bridge.handle(
        {"api_version": "1.1", "command": "format_schema", "args": {"name": "mutation-plan"}}
    )
    assert refused["error"]["code"] == "input.unknown_format"


# ---- deliberate negative tests: the checks fail when 1.1 is broken --------------------------


def _generated() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(all_schemas())


def test_the_check_fails_when_a_1_1_result_field_is_removed_renamed_or_retyped():
    current = _generated()
    props = current["commands/proposals_list.result.schema.json"]["properties"]
    props["rows"] = props.pop("proposals")
    current["commands/project_show.result.schema.json"]["properties"]["file"]["type"] = "integer"
    losses = contract_losses(current)
    assert any("proposals_list.result.proposals: removed" in loss for loss in losses)
    assert any("project_show.result.file: type" in loss for loss in losses)


def test_the_check_fails_when_a_1_1_command_argument_or_code_is_lost_or_changed():
    current = _generated()
    del current["index.json"]["commands"]["safe_scan"]
    del current["index.json"]["error_codes"]["input.unknown_proposal"]
    del current["index.json"]["warning_codes"]["project_source_not_selected"]
    current["index.json"]["commands"]["design"]["effects"].append("network")
    current["index.json"]["commands"]["verify"]["args"]["source"] = "1.2"
    props = current["commands/proposals_propose.request.schema.json"]["properties"]["args"]
    props["properties"]["kinds"]["items"]["enum"].remove("pii")
    props["required"].append("min_confidence")
    losses = contract_losses(current)
    assert "command safe_scan: removed" in losses
    assert any("input.unknown_proposal" in loss for loss in losses)
    assert any("project_source_not_selected" in loss for loss in losses)
    assert any("design: effects changed" in loss for loss in losses)
    assert any("verify.source: no longer since 1.1" in loss for loss in losses)
    assert any("proposals_propose.args.kinds[]: enum lost" in loss for loss in losses)
    assert any("proposals_propose.args: now requires" in loss for loss in losses)


def test_the_replay_fails_when_a_1_1_command_adds_a_1_2_field(tmp_path, monkeypatch):
    """A 1.1 request must be answered exactly as 1.1 answers it: an added field is a failure."""
    doc = json.loads((FROZEN_VECTORS / "format_schema.json").read_text())
    one = next(c for c in doc["cases"] if c["response"]["ok"])
    bridge = Bridge(tmp_path)
    actual = bridge.handle(one["request"])
    assert differences(without_version(one["response"]), without_version(actual)) == []
    actual["result"]["added_in_1_2"] = True
    assert differences(without_version(one["response"]), without_version(actual))
    broken = copy.deepcopy(one["response"])
    broken["result"].pop(next(iter(broken["result"])))
    assert differences(without_version(broken), without_version(bridge.handle(one["request"])))

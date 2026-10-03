"""Bridge 1.1 keeps the 1.0 promise (W7-04, items 1 and 2).

``docs/bridge/schema/1.0/`` and ``docs/bridge/vectors/1.0/`` are the 1.0 contract, frozen: this
file replays every 1.0 vector against the 1.1 bridge and holds the 1.1 schemas to the 1.0 ones. A
change that removes, renames, retypes or tightens something of 1.0 fails here; the deliberate
negative tests at the end show that it does.
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

from shape.bridge.core import Bridge
from shape.bridge.protocol import ERROR_CODES, WARNING_CODES
from shape.bridge.schemas import all_schemas

DOCS = Path(__file__).resolve().parents[2] / "docs" / "bridge"
FROZEN_SCHEMAS = DOCS / "schema" / "1.0"
FROZEN_VECTORS = DOCS / "vectors" / "1.0"
ANY = "<any>"

#: The warning codes of 1.0 (what ``docs/BRIDGE.md`` and the 1.0 handlers define), frozen here.
WARNING_CODES_1_0 = (
    "api_version_assumed",
    "newer_minor_version",
    "artifact_not_verified",
    "empty_table",
    "profile_file_holds_values",
    "domain_load_failed",
    "output_dir_ignored",
    "result_in_file",
)


def frozen_vector_files() -> list[Path]:
    return sorted(FROZEN_VECTORS.glob("*.json"))


def substitute(value: Any, directory: str) -> Any:
    if isinstance(value, str):
        return value.replace("${DIR}", directory)
    if isinstance(value, list):
        return [substitute(v, directory) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, directory) for k, v in value.items()}
    return value


def differences(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Every difference: unlike the vector runner of the schema tests, an extra field in the
    actual response is a difference too (a 1.0 request is answered exactly as 1.0 answers it)."""
    if expected == ANY:
        return []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {actual!r}"]
        out = [f"{path}: unexpected field {k!r}" for k in sorted(set(actual) - set(expected))]
        for key, value in expected.items():
            if key not in actual:
                out.append(f"{path}: missing {key!r}")
            else:
                out.extend(differences(value, actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: expected {expected!r}, got {actual!r}"]
        return [
            d
            for i, (e, a) in enumerate(zip(expected, actual, strict=True))
            for d in differences(e, a, f"{path}[{i}]")
        ]
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def without_version(response: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in response.items() if k != "api_version"}


# ---- the frozen files are the 1.0 contract -------------------------------------------------


def test_the_frozen_1_0_files_are_there_and_say_1_0():
    index = json.loads((FROZEN_SCHEMAS / "index.json").read_text())
    assert index["api_version"] == "1.0" and len(index["commands"]) == 24
    assert len(frozen_vector_files()) == 24
    for path in frozen_vector_files():
        doc = json.loads(path.read_text())
        assert doc["api_version"] == "1.0" and doc["command"] == path.stem
        assert all(c["request"]["api_version"] == "1.0" for c in doc["cases"])
        assert all(c["response"]["api_version"] == "1.0" for c in doc["cases"])
    for path in FROZEN_SCHEMAS.rglob("*.json"):
        assert json.loads(path.read_text()).get("x-api-version", "1.0") == "1.0", path


def test_the_frozen_schemas_name_no_1_1_command():
    index = json.loads((FROZEN_SCHEMAS / "index.json").read_text())
    current = json.loads((DOCS / "schema" / "index.json").read_text())
    assert set(index["commands"]) <= set(current["commands"])
    assert not [n for n in index["commands"] if current["commands"][n]["since"] != "1.0"]


# ---- every 1.0 vector, replayed against the 1.1 bridge ---------------------------------------


@pytest.mark.parametrize("path", frozen_vector_files(), ids=lambda p: p.stem)
def test_every_1_0_vector_gets_the_recorded_response_from_the_1_1_bridge(
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
        assert one["request"]["api_version"] == "1.0"
        if one.get("needs") == "fabric":
            monkeypatch.setattr("shape.scale.http.urllib_transport", FakeFabric())
        actual = bridge.handle(substitute(one["request"], str(directory)))
        expected = substitute(one["response"], str(directory))
        assert actual["api_version"] == "1.1"  # the one field that differs
        assert differences(without_version(expected), without_version(actual)) == [], (
            path.stem,
            one["name"],
        )
    deadline = time.time() + 60
    while time.time() < deadline and any(j["status"] == "running" for j in bridge.jobs.list()):
        time.sleep(0.05)


# ---- the 1.1 schemas are a superset of the 1.0 schemas ------------------------------------------


def frozen_index() -> dict[str, Any]:
    return json.loads((FROZEN_SCHEMAS / "index.json").read_text())


def frozen(rel: str) -> dict[str, Any]:
    return json.loads((FROZEN_SCHEMAS / rel).read_text())


def _types(schema: dict[str, Any]) -> set[str]:
    t = schema.get("type")
    return set(t if isinstance(t, list) else [t]) if t is not None else set()


def schema_losses(old: Any, new: Any, path: str, *, request: bool) -> list[str]:
    """What ``old`` (a 1.0 schema) has that ``new`` (the 1.1 one) lost or tightened.

    A request schema may gain optional properties and values; it may not lose a property, change
    a type, lose an enum value, raise a minimum, change a default or start requiring something.
    A result schema may gain properties; it may not lose one, change a type or stop requiring a
    field it required."""
    out: list[str] = []
    if not isinstance(old, dict):
        return out
    if not isinstance(new, dict):
        return [f"{path}: schema lost"]
    if _types(old) and _types(new) != _types(old):
        out.append(f"{path}: type {sorted(_types(old))} became {sorted(_types(new))}")
    if "enum" in old and not set(map(json.dumps, old["enum"])) <= set(
        map(json.dumps, new.get("enum", []))
    ):
        out.append(f"{path}: enum lost a value")
    if "const" in old and new.get("const") != old["const"]:
        out.append(f"{path}: const changed")
    if "default" in old and new.get("default") != old["default"]:
        out.append(f"{path}: default changed")
    if "minimum" in old and ("minimum" not in new or new["minimum"] > old["minimum"]):
        out.append(f"{path}: minimum raised or removed")
    old_required, new_required = set(old.get("required", [])), set(new.get("required", []))
    if request:
        if new_required - old_required:
            out.append(f"{path}: now requires {sorted(new_required - old_required)}")
    elif old_required - new_required:
        out.append(f"{path}: no longer requires {sorted(old_required - new_required)}")
    for key, sub in old.get("properties", {}).items():
        if key not in new.get("properties", {}):
            out.append(f"{path}.{key}: removed")
        else:
            out.extend(schema_losses(sub, new["properties"][key], f"{path}.{key}", request=request))
    if isinstance(old.get("items"), dict):
        out.extend(schema_losses(old["items"], new.get("items"), f"{path}[]", request=request))
    ap = old.get("additionalProperties")
    if isinstance(ap, dict):
        out.extend(schema_losses(ap, new.get("additionalProperties"), f"{path}.*", request=request))
    if request and old.get("additionalProperties") is False and new.get("additionalProperties"):
        out.append(f"{path}: additionalProperties loosened")
    for key in ("anyOf", "oneOf"):
        if key in old:
            if len(new.get(key, [])) < len(old[key]):
                out.append(f"{path}: {key} lost a branch")
            else:
                for i, branch in enumerate(old[key]):
                    out.extend(
                        schema_losses(branch, new[key][i], f"{path}.{key}[{i}]", request=request)
                    )
    return out


def contract_losses(current: dict[str, dict[str, Any]]) -> list[str]:
    """Everything 1.0 promised that ``current`` (the generated 1.1 schemas) no longer does."""
    losses: list[str] = []
    old_index, new_index = frozen_index(), current["index.json"]
    for name, entry in old_index["commands"].items():
        if name not in new_index["commands"]:
            losses.append(f"command {name}: removed")
            continue
        new_entry = new_index["commands"][name]
        for key in ("job", "cancellable", "pending"):
            if new_entry[key] != entry[key]:
                losses.append(f"command {name}: {key} changed")
        old_request = frozen(entry["request"])["properties"]["args"]
        new_request = current[entry["request"]]["properties"]["args"]
        losses.extend(schema_losses(old_request, new_request, f"{name}.args", request=True))
        for arg in old_request["properties"]:
            if new_index["commands"][name]["args"].get(arg) != "1.0":
                losses.append(f"{name}.{arg}: no longer since 1.0")
        losses.extend(
            schema_losses(
                frozen(entry["result"]),
                current[entry["result"]],
                f"{name}.result",
                request=False,
            )
        )
    for code, meaning in old_index["error_codes"].items():
        if new_index["error_codes"].get(code) != meaning:
            losses.append(f"error code {code}: removed or its meaning changed")
    return losses


def test_the_1_1_schemas_keep_everything_1_0_promised():
    assert contract_losses(all_schemas()) == []


def test_every_1_0_error_code_and_warning_code_is_still_there_with_its_meaning():
    for code, meaning in frozen_index()["error_codes"].items():
        assert ERROR_CODES[code] == meaning
    assert set(WARNING_CODES_1_0) <= set(WARNING_CODES)
    published = all_schemas()["index.json"]["warning_codes"]
    assert published == dict(sorted(WARNING_CODES.items()))


def test_the_1_0_commands_and_arguments_are_published_as_since_1_0():
    index = all_schemas()["index.json"]
    old = frozen_index()
    for name, entry in old["commands"].items():
        assert index["commands"][name]["since"] == "1.0"
        request = frozen(entry["request"])["properties"]["args"]["properties"]
        assert all(index["commands"][name]["args"][a] == "1.0" for a in request)


# ---- deliberate negative tests: the check fails when 1.0 is broken ---------------------------


def _generated() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(all_schemas())


def test_the_check_fails_when_a_1_0_result_field_is_removed():
    current = _generated()
    del current["commands/describe.result.schema.json"]["properties"]["tables"]
    assert any("describe.result.tables: removed" in loss for loss in contract_losses(current))


def test_the_check_fails_when_a_1_0_result_field_is_renamed():
    current = _generated()
    props = current["commands/diff.result.schema.json"]["properties"]
    props["changed"] = props.pop("drifted")
    losses = contract_losses(current)
    assert any("diff.result.drifted: removed" in loss for loss in losses)


def test_the_check_fails_when_a_nested_1_0_result_field_is_removed():
    current = _generated()
    gates = current["commands/verify.result.schema.json"]["properties"]["gates"]["anyOf"][0]
    del gates["items"]["properties"]["errors"]
    assert any("errors: removed" in loss for loss in contract_losses(current))


def test_the_check_fails_when_a_result_field_is_retyped():
    current = _generated()
    current["commands/check.result.schema.json"]["properties"]["passed"]["type"] = "string"
    assert any("check.result.passed: type" in loss for loss in contract_losses(current))


def test_the_check_fails_when_a_result_field_is_no_longer_required():
    current = _generated()
    current["commands/list.result.schema.json"]["required"].remove("domains")
    assert any("no longer requires" in loss for loss in contract_losses(current))


def test_the_check_fails_when_an_argument_is_removed_renamed_or_retyped():
    current = _generated()
    props = current["commands/profile.request.schema.json"]["properties"]["args"]["properties"]
    props["input"] = props.pop("source")
    props["dataset"]["type"] = "string"
    losses = contract_losses(current)
    assert any("profile.args.source: removed" in loss for loss in losses)
    assert any("profile.args.dataset: type" in loss for loss in losses)


def test_the_check_fails_when_a_request_is_tightened():
    current = _generated()
    args = current["commands/describe.request.schema.json"]["properties"]["args"]
    args["required"].append("scale")  # a new required argument
    args["properties"]["mode"]["enum"] = ["3nf"]  # a lost value
    args["properties"]["scale"]["default"] = "small"
    current["commands/preview.request.schema.json"]["properties"]["args"]["properties"]["rows"][
        "minimum"
    ] = 5
    losses = contract_losses(current)
    assert any("describe.args: now requires ['scale']" in loss for loss in losses)
    assert any("describe.args.mode: enum lost" in loss for loss in losses)
    assert any("preview.args.rows: minimum raised" in loss for loss in losses)


def test_the_check_fails_when_a_command_error_code_or_enum_is_removed():
    current = _generated()
    del current["index.json"]["commands"]["check"]
    del current["index.json"]["error_codes"]["input.not_found"]
    losses = contract_losses(current)
    assert "command check: removed" in losses
    assert any("input.not_found" in loss for loss in losses)


def test_the_check_fails_when_a_command_changes_its_job_mode():
    current = _generated()
    current["index.json"]["commands"]["verify"]["job"] = "never"
    assert any("verify: job changed" in loss for loss in contract_losses(current))


def test_a_replayed_vector_fails_on_any_difference():
    expected = {"ok": True, "result": {"a": 1}, "warnings": []}
    assert differences(expected, {"ok": True, "result": {"a": 1}, "warnings": []}) == []
    assert differences(expected, {"ok": True, "result": {"a": 1, "b": 2}, "warnings": []})
    assert differences(expected, {"ok": True, "result": {}, "warnings": []})
    assert differences(expected, {"ok": True, "result": {"a": 1}, "warnings": [{"code": "x"}]})

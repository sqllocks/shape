"""The ``shape-live-check`` result format: what a live check writes, and that it stays readable."""

import json
from pathlib import Path

import pytest
from shape_fabric import livecheck
from shape_fabric.livecheck import LiveCheck, LiveCheckError, load, scrub

FORMATS = Path(__file__).parent / "live_check_formats"
GUID = "11111111-2222-4333-8444-555555555555"


def test_a_passing_check_records_format_version_and_steps(tmp_path):
    check = LiveCheck("fabric-git-sync", "0.9.0")
    with check.step("push"):
        pass
    with check.step("update-from-git"):
        pass
    doc = load(check.write(tmp_path / "r.json"))
    assert (doc["format"], doc["version"], doc["check"]) == (
        "shape-live-check",
        1,
        "fabric-git-sync",
    )
    assert doc["passed"] is True and doc["shape_version"] == "0.9.0"
    assert [s["name"] for s in doc["steps"]] == ["push", "update-from-git"]
    assert doc["started"].endswith("Z") and doc["finished"].endswith("Z")
    assert isinstance(doc["version"], int)


def test_a_failing_step_fails_the_check_and_reraises():
    check = LiveCheck("fabric-git-sync", "0.9.0")
    with check.step("push"):
        pass
    with pytest.raises(RuntimeError), check.step("update-from-git"):
        raise RuntimeError("boom")
    result = check.result()
    assert result["passed"] is False
    assert result["steps"][-1]["passed"] is False and "boom" in result["steps"][-1]["error"]


def test_a_check_with_no_steps_did_not_pass():
    assert LiveCheck("fabric-git-sync", "0.9.0").result()["passed"] is False


def test_the_file_holds_no_secret_tenant_or_workspace_id():
    secret = "s3cr3t-value-123"
    check = LiveCheck("fabric-git-sync", "0.9.0", secrets=[secret])
    with pytest.raises(RuntimeError), check.step("update-from-git"):
        raise RuntimeError(f"workspace {GUID} refused {secret}; other id {GUID.upper()}")
    text = json.dumps(check.result())
    assert secret not in text and GUID not in text and GUID.upper() not in text
    assert "<id>" in text


def test_scrub_boundaries():
    assert scrub("x", [""]) == "x"  # an empty secret is ignored, not replaced everywhere
    assert scrub("abc", ["abc"]) == "abc"  # shorter than four characters: left alone
    assert scrub("abcd", ["abcd"]) == "<secret>"
    assert len(scrub("a" * 1000)) == 303


def test_the_v1_file_written_today_is_still_readable():
    doc = load(FORMATS / "shape-live-check-v1.json")
    assert doc["version"] == 1 and doc["passed"] is True and len(doc["steps"]) == 4


def test_the_fields_of_version_1_are_exactly_the_documented_ones():
    check = LiveCheck("fabric-git-sync", "0.9.0")
    with check.step("push"):
        pass
    written = check.result()
    frozen = json.loads((FORMATS / "shape-live-check-v1.json").read_text())
    assert set(written) == set(frozen)
    assert set(written["steps"][0]) <= {"name", "passed", "seconds", "error"}


def write(tmp_path, doc):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
    return path


def frozen():
    return json.loads((FORMATS / "shape-live-check-v1.json").read_text())


def test_a_newer_version_is_refused_with_a_clear_message(tmp_path):
    doc = frozen() | {"version": livecheck.VERSION + 1}
    with pytest.raises(LiveCheckError, match="newer Shape.*upgrade"):
        load(write(tmp_path, doc))


@pytest.mark.parametrize("version", ["1", 1.0, True, None, 0, -1])
def test_a_version_that_is_not_a_positive_integer_is_refused(tmp_path, version):
    with pytest.raises(LiveCheckError, match="version"):
        load(write(tmp_path, frozen() | {"version": version}))


def test_another_format_or_a_broken_file_is_refused(tmp_path):
    with pytest.raises(LiveCheckError, match="not a shape-live-check"):
        load(write(tmp_path, frozen() | {"format": "shape-profile"}))
    with pytest.raises(LiveCheckError, match="not a readable JSON"):
        load(write(tmp_path, "{nope"))
    with pytest.raises(LiveCheckError, match="not a readable JSON"):
        load(tmp_path / "missing.json")
    with pytest.raises(LiveCheckError, match="not a shape-live-check"):
        load(write(tmp_path, "[1]"))


def test_a_missing_field_or_bad_steps_is_refused(tmp_path):
    doc = frozen()
    del doc["finished"]
    with pytest.raises(LiveCheckError, match="missing finished"):
        load(write(tmp_path, doc))
    with pytest.raises(LiveCheckError, match="steps"):
        load(write(tmp_path, frozen() | {"steps": [{"name": "x"}]}))
    with pytest.raises(LiveCheckError, match="steps"):
        load(write(tmp_path, frozen() | {"steps": "none"}))


def test_unknown_extra_fields_are_tolerated(tmp_path):
    assert load(write(tmp_path, frozen() | {"note": "added later"}))["note"] == "added later"

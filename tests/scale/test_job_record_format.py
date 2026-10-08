"""A job record declares ``format`` and ``version`` like every persisted file, refuses a newer
version by name, and keeps the fields of a newer writer (HUNT2-fabric #636)."""

from __future__ import annotations

import json

import pytest

from shape.compat import FormatError, UnsupportedVersionError
from shape.scale.jobs import JobRecord, JobStore


def _doc(tmp_path, job_id="local-aaaa"):
    return json.loads((tmp_path / f"{job_id}.json").read_text(encoding="utf-8"))


def test_a_record_declares_its_format_and_an_integer_version(tmp_path):
    JobStore(tmp_path).put(JobRecord("local-aaaa", "local"))
    doc = _doc(tmp_path)
    assert doc["format"] == "shape-job"
    assert doc["version"] == 1 and type(doc["version"]) is int
    assert doc["shape_version"] and doc["min_shape_version"]


def test_a_newer_version_is_refused_with_the_release_that_reads_it(tmp_path):
    JobStore(tmp_path).put(JobRecord("local-aaaa", "local"))
    doc = _doc(tmp_path)
    doc.update(version=99, min_shape_version="9.9.0")
    (tmp_path / "local-aaaa.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(UnsupportedVersionError, match=r"version 99.*9\.9\.0") as caught:
        JobStore(tmp_path).get("local-aaaa")
    assert isinstance(caught.value, ValueError)
    assert "local-aaaa" in str(caught.value)


@pytest.mark.parametrize("version", [0, -1, "1", 1.5, True, None])
def test_a_malformed_version_is_refused(tmp_path, version):
    JobStore(tmp_path).put(JobRecord("local-aaaa", "local"))
    doc = _doc(tmp_path)
    doc["version"] = version
    (tmp_path / "local-aaaa.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(FormatError):
        JobStore(tmp_path).get("local-aaaa")


def test_a_record_from_before_the_declaration_still_loads(tmp_path):
    (tmp_path / "local-bbbb.json").write_text(
        json.dumps({"job_id": "local-bbbb", "kind": "local", "status": "failed", "attempts": 2}),
        encoding="utf-8",
    )
    store = JobStore(tmp_path)
    assert store.get("local-bbbb").attempts == 2
    store.update("local-bbbb", status="submitted")
    assert _doc(tmp_path, "local-bbbb")["format"] == "shape-job"


def test_the_fields_of_a_newer_writer_survive_an_update(tmp_path):
    JobStore(tmp_path).put(JobRecord("local-aaaa", "local"))
    doc = _doc(tmp_path)
    doc["future_field"] = {"x": [1, 2]}
    (tmp_path / "local-aaaa.json").write_text(json.dumps(doc), encoding="utf-8")
    store = JobStore(tmp_path)
    store.update("local-aaaa", status="running")
    after = _doc(tmp_path)
    assert after["future_field"] == {"x": [1, 2]}
    assert after["status"] == "running"
    assert "extra" not in after


def test_a_file_of_another_kind_is_not_read_as_a_job(tmp_path):
    (tmp_path / "local-cccc.json").write_text(
        json.dumps({"format": "shape-contract", "version": 1, "job_id": "local-cccc", "kind": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(FormatError):
        JobStore(tmp_path).get("local-cccc")


def test_listing_skips_a_record_it_cannot_read(tmp_path):
    store = JobStore(tmp_path)
    store.put(JobRecord("local-aaaa", "local"))
    newer = _doc(tmp_path)
    newer.update(job_id="local-dddd", version=99)
    (tmp_path / "local-dddd.json").write_text(json.dumps(newer), encoding="utf-8")
    assert [r.job_id for r in JobStore(tmp_path).list()] == ["local-aaaa"]

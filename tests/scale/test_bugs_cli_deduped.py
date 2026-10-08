"""BUGS-cli-1 #543: Fabric's ``Deduped`` job status maps to a final status Shape knows."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fakes import NB, RUN, WS, FakeFabric

from shape.scale.jobs import ACTIVE, FINAL, STATUS_MAP, FabricJobTracker, Jobs, JobStore


def _spark_job(store: JobStore) -> None:
    """``spark_job`` of this folder's ``test_jobs.py``, loaded by path: ``tests/bridge`` has a
    ``test_jobs`` module too, and a bare import takes whichever was loaded first."""
    spec = importlib.util.spec_from_file_location(
        "scale_test_jobs", Path(__file__).with_name("test_jobs.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.spark_job(store)


# Every status Fabric documents for a job instance.
FABRIC_STATUSES = ("NotStarted", "InProgress", "Completed", "Failed", "Cancelled", "Deduped")


@pytest.mark.parametrize("raw", FABRIC_STATUSES)
def test_every_documented_fabric_status_maps_to_a_known_status(raw):
    fake = FakeFabric()
    fake.job_status = raw
    status = FabricJobTracker("tok", fake).get_status(WS, NB, RUN)["status"]
    assert status in ACTIVE + FINAL


def test_deduped_is_cancelled_and_keeps_the_fabric_name():
    fake = FakeFabric()
    fake.job_status = "Deduped"
    got = FabricJobTracker("tok", fake).get_status(WS, NB, RUN)
    assert got["status"] == "cancelled"
    assert got["fabric_status"] == "Deduped"


def test_the_map_only_holds_known_statuses():
    assert set(STATUS_MAP.values()) <= set(ACTIVE + FINAL)


def test_a_deduped_job_is_final_and_not_polled_again(tmp_path):
    fake = FakeFabric()
    store = JobStore(tmp_path)
    jobs = Jobs(store, transport=fake)
    _spark_job(store)
    fake.job_status = "Deduped"
    assert jobs.status("spark-t1", "tok")["status"] == "cancelled"
    polls = len(fake.calls)
    assert jobs.status("spark-t1", "tok")["status"] == "cancelled"
    assert len(fake.calls) == polls  # final: no more polling


def test_a_deduped_job_can_be_resumed(tmp_path):
    fake = FakeFabric()
    store = JobStore(tmp_path)
    jobs = Jobs(store, transport=fake)
    _spark_job(store)
    fake.job_status = "Deduped"
    jobs.status("spark-t1", "tok")
    submitted = []
    out = jobs.resume_spark("spark-t1", "tok", lambda req: submitted.append(req) or {})
    assert len(submitted) == 1
    assert out["status"] == "submitted"

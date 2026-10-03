"""``normalize`` rejects a request that cannot run, naming the setting (HUNT2-fabric #704)."""

from __future__ import annotations

import pytest

from shape.scale.api import normalize, scale_generate
from shape.scale.jobs import Jobs, JobStore

BASE = {"domain": "retail", "scale": "small", "sinks": ["memory"]}


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_workers", 0),
        ("max_workers", "2x"),
        ("max_workers", 1.5),
        ("max_workers", True),
        ("processes", -1),
        ("processes", "x"),
        ("processes", 1.5),
        ("chunk_size", "abc"),
        ("chunk_size", 2.5),
        ("chunk_size", True),
        ("chunk_size", 0),
        ("seed", "abc"),
        ("seed", 1.5),
        ("seed", True),
    ],
)
def test_a_bad_number_is_refused_by_name(setting, value):
    with pytest.raises(ValueError, match=setting):
        normalize({**BASE, setting: value})


@pytest.mark.parametrize(
    ("setting", "value"),
    [("sink_config", [1]), ("domain", ["retail"]), ("sinks", ["memory", 3])],
)
def test_a_bad_shape_is_refused_by_name(setting, value):
    with pytest.raises(ValueError, match=setting.split("_")[0]):
        normalize({**BASE, setting: value})


def test_no_job_is_made_for_a_request_that_cannot_run(tmp_path):
    jobs = Jobs(JobStore(tmp_path))
    with pytest.raises(ValueError, match="processes"):
        scale_generate({**BASE, "processes": -1}, jobs=jobs)
    assert jobs.store.list() == []


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_workers", 2),
        ("max_workers", "2"),
        ("processes", 0),
        ("chunk_size", 1000),
        ("chunk_size", "1000"),
        ("seed", 7),
        ("seed", -3),
        ("seed", 2**70),
        ("seed", "7"),
    ],
)
def test_a_good_number_is_kept(setting, value):
    assert normalize({**BASE, setting: value})[setting] in (value, int(value))

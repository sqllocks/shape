import json
import zipfile

import pytest

from shape.artifact import read_shape, write_shape
from shape.privacy import redact_sensitive
from shape.streaming import PartitionedKeyedState


def test_shape_rejects_newer_format(tmp_path):
    p = tmp_path / "x.shape"
    write_shape(p, {"rows": 1, "columns": {}})
    q = tmp_path / "new.shape"
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(q, "w") as zout:
        for i in zin.infolist():
            b = zin.read(i.filename)
            if i.filename == "manifest.json":
                m = json.loads(b)
                m["format_version"] = 999
                b = json.dumps(m, separators=(",", ":"), sort_keys=True).encode()
            zout.writestr(i.filename, b)
    with pytest.raises(ValueError, match="unsupported Shape artifact version"):
        read_shape(q)


def test_redaction_many_sensitive_values():
    vals = [f"user{i}@example.com" for i in range(100)]
    shape = {
        "rows": 100,
        "columns": {"email": {"topk": [(x, 1) for x in vals], "examples": vals[:5], "count": 100}},
    }
    raw = json.dumps(redact_sensitive(shape, {"email": "TOP_SECRET"}))
    assert all(x not in raw for x in vals)


def test_partition_state_capacity_never_exceeded():
    s = PartitionedKeyedState(16, 60, 50)
    for i in range(100000):
        s.put(i, i, float(i))
    assert len(s) <= 800


def test_partition_assignment_survives_restore():
    s = PartitionedKeyedState(32, 1000, 100)
    keys = [f"k{i}" for i in range(1000)]
    before = [s.partition_for(k) for k in keys]
    r = PartitionedKeyedState.restore(s.snapshot())
    assert before == [r.partition_for(k) for k in keys]

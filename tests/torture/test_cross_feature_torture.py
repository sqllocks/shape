import random

import numpy as np
import pytest

from shape.artifact import read_shape, write_shape
from shape.builtins.strategies.address import AddressReference, FastAddressPack
from shape.capture import capture_rows
from shape.connectors.qualification import ConnectorRecord, ExactlyOnceProjector
from shape.contracts import compatibility
from shape.generation.joint import JointModel, generate_joint_numeric
from shape.generation.relational import (
    generate_composite_keys,
    generate_fk_indices,
    materialize_composite_fks,
)
from shape.location import Location, LocationScope
from shape.privacy import release_for
from shape.query import query

REF = [
    AddressReference(
        "100 High St",
        "Columbus",
        "Franklin",
        "OH",
        "43215",
        "US",
        39.9612,
        -82.9988,
        "America/New_York",
        "1",
    ),
    AddressReference(
        "1 Main St",
        "Dublin",
        "Franklin",
        "OH",
        "43017",
        "US",
        40.0992,
        -83.1141,
        "America/New_York",
        "2",
    ),
]
PACK = FastAddressPack(REF, "t")
SCOPE = LocationScope.weighted([(Location.zip("43215"), 0.7), (Location.zip("43017"), 0.3)])


@pytest.mark.parametrize("seed", range(50))
def test_generation_relations_location_privacy_artifact(seed, tmp_path):
    n = 2000
    pk = generate_composite_keys(n, 3, seed * n)
    parent = generate_composite_keys(200, 3, seed)
    ix = generate_fk_indices(200, n, seed, 0.4)
    fk = materialize_composite_fks(parent, ix)
    a = PACK.generate_columns(n, SCOPE, seed, encoded=True)
    j = generate_joint_numeric(
        JointModel(("x", "y"), (10.0, 20.0), (2.0, 4.0), ((1.0, 0.7), (0.7, 1.0))), n, seed
    )
    rows = [
        {
            "id": int(pk[0][i]),
            "x": float(j["x"][i]),
            "y": float(j["y"][i]),
            "lat": float(a["latitude"][i]),
            "lon": float(a["longitude"][i]),
        }
        for i in range(n)
    ]
    s = capture_rows(rows).to_dict()
    denied = release_for(s, {"x": "SENSITIVE"}, "INTERNAL", source_classification="SENSITIVE")
    assert denied.allowed is False and denied.reason == "source_exceeds_target"
    r = release_for(s, {"x": "SENSITIVE"}, "INTERNAL", source_classification="INTERNAL")
    assert r.allowed is True
    p = tmp_path / f"{seed}.shape"
    write_shape(p, r.shape, name="t", classification="INTERNAL")
    _, back = read_shape(p)
    assert back["rows"] == n and len(set(zip(pk[0], pk[1], pk[2], strict=False))) == n
    sample = np.arange(0, n, 31)
    assert np.array_equal(fk[0][sample], parent[0][ix[sample]])
    assert np.isfinite(a["latitude"]).all()
    assert abs(np.corrcoef(j["x"], j["y"])[0, 1] - 0.7) < 0.08


@pytest.mark.parametrize("seed", range(50))
def test_capture_query_contract(seed):
    rng = random.Random(seed)
    rows = [
        {"id": i, "v": rng.randint(-1000, 1000), "cat": str(rng.randrange(20))} for i in range(5000)
    ]
    s = capture_rows(rows).to_dict()
    assert s["rows"] == 5000
    assert query(s, 'column("v").mean') is not None
    assert isinstance(compatibility(s, s, "full").compatible, bool)


def test_stream_replay_failure_recovery():
    seen = []
    p = ExactlyOnceProjector()
    rec = [ConnectorRecord(str(i % 8), i // 8, i, str(i)) for i in range(10000)]
    p.process(rec, seen.append)
    p.process(rec, seen.append)
    assert seen == list(range(10000))

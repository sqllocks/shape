"""shape.save / shape.load: round trips, explicit NaN and infinity, integrity checks."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.artifact import ArtifactError
from shape.profile.reference import Profile


def test_round_trip_equality(tmp_path: Path, orders):
    p = shape.profile(orders, name="orders")
    path = tmp_path / "orders.shape"
    content_id = shape.save(p, path)
    assert len(content_id) == 64
    q = shape.load(path)
    assert q == p
    assert q.to_dict() == p.to_dict()
    assert q.summary() == p.summary()
    assert q.name == "orders"


def test_round_trip_preserves_frequency_order(tmp_path: Path):
    values = ["c"] * 5 + ["a"] * 3 + ["b"] * 9
    p = shape.profile(pa.table({"s": values}))
    shape.save(p, tmp_path / "x.shape")
    q = shape.load(tmp_path / "x.shape")
    assert list(q.to_dict()["columns"]["s"]["enum_values"]) == ["b", "c", "a"]


def test_round_trip_multi_table(tmp_path: Path, orders, customers):
    p = shape.profile({"orders": orders, "customer": customers})
    shape.save(p, tmp_path / "m.shape")
    q = shape.load(tmp_path / "m.shape")
    assert q == p and q.is_dataset
    assert q.to_dict()["relationships"] == p.to_dict()["relationships"]


def test_nan_and_infinity_round_trip(tmp_path: Path):
    t = pa.table({"x": pa.array([1.0, float("inf"), 3.0, float("-inf"), float("nan"), 5.0] * 10)})
    p = shape.profile(t)
    assert p.to_dict()["columns"]["x"]["std"] == "NaN"
    shape.save(p, tmp_path / "n.shape")
    q = shape.load(tmp_path / "n.shape")
    assert q == p
    assert q.to_dict()["columns"]["x"]["min_value"] == ["float", -math.inf]


def test_raw_float_nan_and_inf_are_encoded_explicitly(tmp_path: Path):
    data = {
        "name": "t",
        "row_count": 1,
        "primary_key": [],
        "detected_fks": {},
        "correlation_matrix": None,
        "columns": {"a": {"mean": float("nan"), "std": float("inf"), "lo": float("-inf")}},
    }
    p = Profile(data)
    path = tmp_path / "r.shape"
    shape.save(p, path)
    with zipfile.ZipFile(path) as z:
        body = z.read("profile.json").decode()
    assert "NaN" not in body and "Infinity" not in body  # never bare JSON extensions
    assert '"$float":"nan"' in body and '"$float":"inf"' in body and '"$float":"-inf"' in body
    q = shape.load(path)
    col = q.tables["t"]["columns"]["a"]
    assert math.isnan(col["mean"]) and col["std"] == math.inf and col["lo"] == -math.inf
    assert q == p


def test_artifact_is_a_shape_manifest(tmp_path: Path, orders):
    path = tmp_path / "o.shape"
    shape.save(shape.profile(orders), path)
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "shape"
    assert manifest["kind"] == "profile"
    assert manifest["format_version"] == 1


def test_load_rejects_non_profile_and_tampered_files(tmp_path: Path, orders):
    from shape.artifact import write_shape

    other = tmp_path / "legacy.shape"
    write_shape(other, {"rows": 1})
    with pytest.raises(ArtifactError):
        shape.load(other)
    bad = tmp_path / "bad.shape"
    bad.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        shape.load(bad)
    good = tmp_path / "good.shape"
    shape.save(shape.profile(orders), good)
    tampered = tmp_path / "tampered.shape"
    with zipfile.ZipFile(good) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "profile.json":
                data = data.replace(b'"row_count":400', b'"row_count":401')
            zout.writestr(item, data)
    with pytest.raises(ArtifactError):
        shape.load(tampered)


def test_save_requires_a_profile(tmp_path: Path):
    with pytest.raises(TypeError):
        shape.save({"x": 1}, tmp_path / "x.shape")  # type: ignore[arg-type]

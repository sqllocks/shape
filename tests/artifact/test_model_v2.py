"""Shape model v2 and the .shape v2 artifact (P1-09): exact round trips (P8, P20), the v1
migrator, wrapped reader errors (P18)."""

from __future__ import annotations

import ast
import json
import math
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import shape
from shape.artifact import (
    ArtifactError,
    ArtifactFormatError,
    codec,
    read_model,
    read_shape,
    write_model,
    write_shape,
)
from shape.artifact.io import canonical_json, sha256, write_artifact
from shape.capture import capture_rows
from shape.spec.migrate import legacy_view, to_model
from shape.spec.model import ModelError, is_model, model_problems, validate_model

TESTS = Path(__file__).resolve().parents[1]


def same(a, b, ordered: bool = True) -> bool:
    """Equality that sees types (tuple vs list, int vs float) and treats NaN as equal to NaN."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return (math.isnan(a) and math.isnan(b)) or (
            a == b and math.copysign(1, a) == math.copysign(1, b)
        )
    if isinstance(a, dict):
        if (list(a) != list(b)) if ordered else (set(a) != set(b)):
            return False
        return all(same(a[k], b[k], ordered) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y, ordered) for x, y in zip(a, b, strict=True))
    return a == b


KEYS = st.one_of(
    st.text(max_size=8),
    st.sampled_from(["$float", "$tuple", "$dict", "$x", "$", "a$"]),
)
VALUES = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**70), max_value=2**70),
        st.floats(allow_nan=True, allow_infinity=True),
        st.text(max_size=12),
    ),
    lambda kids: st.one_of(
        st.lists(kids, max_size=4),
        st.lists(kids, max_size=4).map(tuple),
        st.dictionaries(KEYS, kids, max_size=4),
    ),
    max_leaves=25,
)


@given(VALUES)
@settings(max_examples=400, deadline=None)
def test_codec_roundtrip_is_exact(value):
    assert same(codec.loads(codec.dumps(value)), value)


def test_codec_refuses_what_it_cannot_restore():
    for bad in ({1: "a"}, {"a": {1, 2}}, b"bytes", object()):
        with pytest.raises(TypeError):
            codec.dumps(bad)
    for text in ('{"$float": "maybe"}', '{"$tuple": 3}', '{"$float": "nan", "x": 1}', "NaN"):
        with pytest.raises(ValueError):
            codec.loads(text)


def _column(name: str, **extra):
    col = {
        "name": name,
        "arrow_type": "double",
        "kind": "float",
        "count": 3,
        "null_count": 0,
        "error_models": {},
    }
    col.update(extra)
    return col


def _model(columns, rows=3):
    return {
        "schema_version": 2,
        "engine": "test",
        "mode": "exact",
        "tables": {"t": {"name": "t", "rows": rows, "columns": columns}},
    }


FLOATS = st.floats(allow_nan=True, allow_infinity=True)


@given(
    st.lists(
        st.fixed_dictionaries(
            {"min": FLOATS, "max": FLOATS, "mean": FLOATS | st.none()},
            optional={
                "top": st.lists(
                    st.lists(FLOATS | st.text(max_size=4), min_size=2, max_size=3).map(tuple),
                    max_size=3,
                )
            },
        ),
        min_size=1,
        max_size=4,
    )
)
@settings(max_examples=150, deadline=None)
def test_model_roundtrips_through_a_file_with_nan_inf_and_tuples(tmp_path_factory, stats):
    tmp = tmp_path_factory.mktemp("rt")
    model = _model([_column(f"c{i}", **s) for i, s in enumerate(stats)])
    cid = write_model(tmp / "m.shape", model, name="m")
    manifest, back = read_model(tmp / "m.shape")
    assert manifest["format_version"] == 2 and manifest["shape_content_id"] == cid
    assert same(back, model, ordered=False)  # shape.json is written with sorted keys


def test_capture_with_nan_and_inf_can_be_saved_and_read_back(tmp_path):
    rows = [
        {"x": float("nan"), "y": 1.0},
        {"x": float("inf"), "y": float("-inf")},
        {"x": 2.0, "y": None},
    ]
    captured = capture_rows(rows).to_dict()
    write_shape(tmp_path / "c.shape", captured, name="c")
    _, back = read_shape(tmp_path / "c.shape")
    assert same(back, captured, ordered=False)
    _, model = read_model(tmp_path / "c.shape")
    assert model["tables"]["c"]["rows"] == 3 and not model_problems(model)


def test_tuples_survive_save_and_load(tmp_path):
    p = shape.profile({"t": __import__("pyarrow").table({"a": [1, 2, 3]})})
    data = p.to_dict()
    data["tables"]["t"]["extra"] = {"pair": (1, 2.5), "nan": float("nan")}
    from shape.profile.reference import Profile

    q = Profile(data)
    shape.save(q, tmp_path / "t.shape")
    back = shape.load(tmp_path / "t.shape")
    assert same(back.to_dict(), q.to_dict())


# -------------------------------------------------------------------------------- migration


def _v1_file(path: Path, body: dict, name="legacy") -> None:
    raw = canonical_json(body)
    manifest = {
        "format": "shape",
        "format_version": 1,
        "name": name,
        "shape_content_id": sha256(raw),
        "fidelity": "gold",
        "classification": "PUBLIC",
        "metadata": {},
    }
    write_artifact(path, manifest, {"shape.json": raw})


def test_a_v1_file_is_read_through_the_migrator(tmp_path):
    body = capture_rows([{"a": i, "b": "x"} for i in range(5)]).to_dict()
    _v1_file(tmp_path / "v1.shape", body)
    manifest, model = read_model(tmp_path / "v1.shape")
    assert manifest["format_version"] == 2 and manifest["migrated_from"] == 1
    assert is_model(model) and model["tables"]["legacy"]["rows"] == 5
    assert [c["name"] for c in model["tables"]["legacy"]["columns"]] == ["a", "b"]
    assert read_shape(tmp_path / "v1.shape")[1] == json.loads(canonical_json(body))


def test_migrated_columns_use_the_engine_vocabulary():
    doc = to_model(capture_rows([{"a": 1.5}, {"a": None}, {"a": 2.5}]).to_dict(), "t")
    (col,) = doc["tables"]["t"]["columns"]
    assert col["kind"] == "float" and col["count"] == 3 and col["null_count"] == 1
    assert col["distinct_exact"] is False and col["quantiles"]["0.5"] == 1.5
    assert col["top"][0][:2] == [1.5, 1] and "hyperloglog" in json.dumps(col["error_models"])


def _literal_v1_shapes():
    found = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "columns" in keys and ({"rows"} & keys or len(keys) == 1):
                try:
                    value = ast.literal_eval(node)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value.get("columns"), dict):
                    found.append((f"{path.relative_to(TESTS)}:{node.lineno}", value))
    return found


def test_every_v1_fixture_in_tests_migrates():
    fixtures = _literal_v1_shapes()
    assert len(fixtures) >= 8, "the scan should find the v1 literals used across the suite"
    for where, fixture in fixtures:
        model = to_model(fixture)
        assert not model_problems(model), where
        assert legacy_view(model) == fixture, where


def test_engine_v1_documents_migrate():
    from shape.profile import engine

    doc = (
        engine.profile_many({"t": __import__("pyarrow").table({"a": [1, 2, 3]})})
        if hasattr(engine, "profile_many")
        else None
    )
    if doc is None:
        pytest.skip("no engine entry point for in-memory tables")
    assert to_model(doc)["schema_version"] == 2


# -------------------------------------------------------------------- reader errors (P18)


def _good(tmp_path) -> Path:
    p = tmp_path / "good.shape"
    write_shape(p, {"rows": 1, "columns": {}})
    return p


def _rewrite(src: Path, dst: Path, **changes):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename in changes:
                data = changes[info.filename](data)
            zout.writestr(info.filename, data)


def test_reader_failures_are_artifact_errors(tmp_path):
    good = _good(tmp_path)
    cases = {}
    cases["not-a-zip"] = b"this is not a zip file"
    cases["truncated"] = good.read_bytes()[:40]
    bad_format = tmp_path / "f.shape"
    _rewrite(
        good,
        bad_format,
        **{"manifest.json": lambda b: json.dumps({**json.loads(b), "format": "other"}).encode()},
    )
    cases["wrong-format"] = bad_format.read_bytes()
    no_component = tmp_path / "n.shape"
    with zipfile.ZipFile(no_component, "w") as z:
        z.writestr(
            "manifest.json",
            json.dumps({"format": "shape", "format_version": 2, "content_hashes": {}}),
        )
    cases["no-component"] = no_component.read_bytes()
    for label, raw in cases.items():
        p = tmp_path / f"{label}.shape"
        p.write_bytes(raw)
        with pytest.raises(ArtifactError):
            read_shape(p)
        with pytest.raises(ValueError):  # ArtifactError stays a ValueError
            read_model(p)


def test_a_body_that_is_not_json_or_has_bad_tags_is_an_artifact_error(tmp_path):
    for body in (b"{not json", b"\xff\xfe", b'{"$float": "x"}', b'{"a": NaN}', b"[1, 2]"):
        raw = body
        manifest = {
            "format": "shape",
            "format_version": 2,
            "name": "b",
            "shape_content_id": sha256(raw),
            "classification": "PUBLIC",
        }
        p = tmp_path / "b.shape"
        write_artifact(p, manifest, {"shape.json": raw})
        with pytest.raises(ArtifactError):
            read_model(p)


def test_a_bad_archive_is_also_a_bad_zip_file(tmp_path):
    p = tmp_path / "x.shape"
    p.write_bytes(b"nope")
    with pytest.raises(zipfile.BadZipFile) as info:
        read_shape(p)
    assert isinstance(info.value, ArtifactFormatError)


def test_corrupt_deflate_data_is_an_artifact_error(tmp_path):
    good = _good(tmp_path)
    raw = bytearray(good.read_bytes())
    marker = raw.find(b"shape.json")
    # flip bytes inside the compressed body that follows the first local header
    for i in range(marker + 10, marker + 40):
        raw[i] ^= 0xFF
    bad = tmp_path / "bad.shape"
    bad.write_bytes(bytes(raw))
    with pytest.raises(ArtifactError):
        read_shape(bad)


def test_model_validation_names_what_is_wrong():
    doc = _model([_column("a")])
    assert validate_model(doc) is doc
    doc["tables"]["t"]["columns"][0]["kind"] = "decimal"
    doc["mode"] = "sketchy"
    with pytest.raises(ModelError) as info:
        validate_model(doc)
    assert "kind" in str(info.value) and "mode" in str(info.value)

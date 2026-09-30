"""One conformance test per normative statement of ``docs/specs/SHAPE_2.md`` (P1-10).

Each function below is registered under the identifier of the statement it tests
(``SH2-001`` ...). ``run_requirements`` runs them all and returns machine-readable
``Check`` results; ``statements`` reads the statements back from the specification (or, where
the repository's docs are absent, from the copy packaged in ``statements.json``), and
``coverage_problems`` compares the two: every statement must have a test and every test a
statement. ``shape conformance`` and ``scripts/check_conformance_coverage.py`` use them.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .conformance import Check, run

REQUIREMENTS: dict[str, Callable[[], None]] = {}

_STATEMENT = re.compile(r"^- \*\*(SH2-\d{3})\*\*\s+(.*)$")
_PACKAGED = Path(__file__).with_name("statements.json")


def requirement(ident: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        if ident in REQUIREMENTS:
            raise ValueError(f"{ident} is tested twice")
        REQUIREMENTS[ident] = fn
        return fn

    return register


# ---------------------------------------------------------------- the specification's side


def parse_statements(text: str) -> dict[str, str]:
    """``{id: statement}`` of the ``- **SH2-nnn** ...`` bullets of the specification. A bullet
    may continue on indented lines. Every line that carries MUST has to belong to one."""
    found: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _STATEMENT.match(line)
        if m:
            current = m.group(1)
            if current in found:
                raise ValueError(f"{current} appears twice in the specification")
            found[current] = m.group(2).strip()
        elif current and line.startswith("  ") and line.strip():
            found[current] += " " + line.strip()
        else:
            current = None
            if re.search(r"\bMUST\b", line):
                raise ValueError(f"a normative line without an identifier: {line.strip()!r}")
    for ident, sentence in found.items():
        if len(re.findall(r"\bMUST\b", sentence)) != 1:
            raise ValueError(f"{ident} must carry exactly one MUST or MUST NOT")
    return found


def spec_path() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "specs" / "SHAPE_2.md"
        if candidate.is_file():
            return candidate
    return None


def statements() -> dict[str, str]:
    """The normative statements: from ``docs/specs/SHAPE_2.md`` when the repository is there,
    else from the packaged copy."""
    path = spec_path()
    if path is not None:
        return parse_statements(path.read_text(encoding="utf-8"))
    data: dict[str, str] = json.loads(_PACKAGED.read_text(encoding="utf-8"))
    return data


def coverage_problems() -> list[str]:
    """Statements without a test and tests without a statement (empty when they match)."""
    spec = statements()
    out = [f"{i}: no conformance test" for i in sorted(set(spec) - set(REQUIREMENTS))]
    out += [
        f"{i}: test without a statement in SHAPE_2.md"
        for i in sorted(set(REQUIREMENTS) - set(spec))
    ]
    return out


def run_requirements() -> list[Check]:
    """Run every registered test; one ``Check`` per statement, in identifier order."""
    return run({i: REQUIREMENTS[i] for i in sorted(REQUIREMENTS)})


# ----------------------------------------------------------------------------- test fixtures


def _capture(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from shape.capture import capture_rows

    return capture_rows(rows).to_dict()


def _model(columns: list[dict[str, Any]], rows: int) -> dict[str, Any]:
    cols = [
        {
            "arrow_type": "int64",
            "kind": "int",
            "count": rows,
            "null_count": 0,
            "error_models": {},
            **c,
        }
        for c in columns
    ]
    return {
        "schema_version": 2,
        "engine": "conformance",
        "mode": "exact",
        "tables": {"t": {"name": "t", "rows": rows, "columns": cols}},
    }


def _engine_doc(mode: str, rows: int = 400, unique_text: bool = False) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from shape.profile.engine import profile

    data = {
        "id": list(range(rows)),
        "name": [f"name-{i}" if unique_text else f"n{i % 7}" for i in range(rows)],
    }
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.parquet"
        pq.write_table(pa.table(data), path)
        return profile(path, mode=mode)


def _file(d: str, name: str = "x.shape") -> Path:
    return Path(d) / name


def _same(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return (math.isnan(a) and math.isnan(b)) or a == b
    if isinstance(a, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _raises(
    exc: type[BaseException] | tuple[type[BaseException], ...], fn: Callable[[], Any]
) -> None:
    try:
        fn()
    except exc:
        return
    raise AssertionError("expected an error that did not happen")


# --------------------------------------------------------------------------------- the model


@requirement("SH2-001")
def _sh2_001() -> None:
    from shape.spec.migrate import to_model
    from shape.spec.model import ModelError, validate_model

    assert to_model(_capture([{"a": 1}]))["schema_version"] == 2
    bad = _model([], 0)
    bad["schema_version"] = 3
    _raises(ModelError, lambda: validate_model(bad))


@requirement("SH2-002")
def _sh2_002() -> None:
    from shape.artifact import ArtifactError, read_model
    from shape.artifact.io import sha256, write_artifact
    from shape.spec.model import ModelError, validate_model

    bad = _model([{"name": "a", "kind": "decimal"}], 1)
    _raises(ModelError, lambda: validate_model(bad))
    body = json.dumps(bad).encode()
    with tempfile.TemporaryDirectory() as d:
        manifest = {"format": "shape", "format_version": 2, "shape_content_id": sha256(body)}
        write_artifact(_file(d), manifest, {"shape.json": body})
        _raises(ArtifactError, lambda: read_model(_file(d)))


@requirement("SH2-003")
def _sh2_003() -> None:
    from shape.spec.migrate import legacy_view, to_model

    v1 = _capture([{"a": 1, "b": "x"}, {"a": 2, "b": None}])
    v1["note"] = {"kept": (1, 2)}
    model = to_model(v1)
    assert model["schema_version"] == 2 and model["tables"]["table"]["rows"] == 2
    assert legacy_view(model) == v1


# ------------------------------------------------------------------------------- the evidence


@requirement("SH2-004")
def _sh2_004() -> None:
    bounded = _engine_doc("bounded")["tables"]["t"]["columns"]
    assert all(c["distinct_exact"] is False for c in bounded)
    captured = _capture([{"a": i} for i in range(5)])
    assert all(c.get("distinct_estimate") is not None for c in captured["columns"].values())
    from shape.spec.migrate import to_model

    assert to_model(captured)["tables"]["table"]["columns"][0]["distinct_exact"] is False


@requirement("SH2-005")
def _sh2_005() -> None:
    for c in _engine_doc("bounded")["tables"]["t"]["columns"]:
        model = c["error_models"]["cardinality"]
        assert model["exact"] is False and model["algorithm"]
        assert isinstance(model["relative_error"], float) and model["relative_error"] > 0


@requirement("SH2-006")
def _sh2_006() -> None:
    for c in _engine_doc("exact")["tables"]["t"]["columns"]:
        assert c["distinct_exact"] is True and c["error_models"]["cardinality"]["exact"] is True
    assert _engine_doc("exact")["tables"]["t"]["columns"][1]["distinct"] == 7


@requirement("SH2-007")
def _sh2_007() -> None:
    rows = 5000
    table = _engine_doc("exact", rows, unique_text=True)["tables"]["t"]
    assert table["rows"] == rows
    for c in table["columns"]:
        assert len(c.get("top", [])) <= 500  # values seen, never the rows
    assert len(json.dumps(table)) < 400_000


@requirement("SH2-032")
def _sh2_032() -> None:
    a = _engine_doc("exact")
    b = _engine_doc("exact")
    assert a == b and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ------------------------------------------------------------------------------- the artifact


def _manifest_version(path: Path) -> int:
    with zipfile.ZipFile(path) as z:
        return int(json.loads(z.read("manifest.json"))["format_version"])


@requirement("SH2-008")
def _sh2_008() -> None:
    from shape.artifact import write_model, write_shape

    with tempfile.TemporaryDirectory() as d:
        write_model(_file(d), _model([], 0))
        assert _manifest_version(_file(d)) == 2
        write_shape(_file(d, "y.shape"), _capture([{"a": 1}]))
        assert _manifest_version(_file(d, "y.shape")) == 2


@requirement("SH2-009")
def _sh2_009() -> None:
    import inspect

    from shape.artifact import shape_file

    assert shape_file.FORMAT_VERSION == 2
    for fn in (shape_file.write_model, shape_file.write_shape):
        assert "format_version" not in inspect.signature(fn).parameters
    with tempfile.TemporaryDirectory() as d:
        shape_file.write_shape(_file(d), {"rows": 1, "format_version": 1})  # a v1 document
        assert _manifest_version(_file(d)) == 2


def _rewrite(src: Path, dst: Path, member: str, change: Callable[[bytes], bytes]) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            zout.writestr(info.filename, change(data) if info.filename == member else data)


@requirement("SH2-010")
def _sh2_010() -> None:
    from shape.artifact import ArtifactError, read_model, write_model

    with tempfile.TemporaryDirectory() as d:
        write_model(_file(d), _model([], 3))
        _rewrite(
            _file(d),
            _file(d, "bad.shape"),
            "shape.json",
            lambda b: b.replace(b'"rows":3', b'"rows":4'),
        )
        _raises(ArtifactError, lambda: read_model(_file(d, "bad.shape")))


@requirement("SH2-011")
def _sh2_011() -> None:
    from shape.artifact import ArtifactError
    from shape.artifact.io import read_artifact

    for name in ("../evil", "a/../evil", "/abs", "a\\b", "C:evil"):
        with tempfile.TemporaryDirectory() as d:
            with zipfile.ZipFile(_file(d), "w") as z:
                z.writestr("manifest.json", '{"content_hashes":{}}')
                z.writestr(name, b"x")
            _raises(ArtifactError, lambda: read_artifact(_file(d)))


@requirement("SH2-012")
def _sh2_012() -> None:
    from shape.artifact import ArtifactError, read_model
    from shape.artifact.io import write_artifact

    body = b'{"schema_version":2}'
    manifest = {"format": "shape", "format_version": 2, "shape_content_id": "0" * 64}
    with tempfile.TemporaryDirectory() as d:
        write_artifact(_file(d), manifest, {"shape.json": body})
        _raises(ArtifactError, lambda: read_model(_file(d)))


@requirement("SH2-013")
def _sh2_013() -> None:
    from shape.artifact import ArtifactError, read_model, read_shape, write_model
    from shape.artifact.io import sha256, write_artifact

    with tempfile.TemporaryDirectory() as d:
        _file(d, "junk.shape").write_bytes(b"not an archive")
        _raises(ArtifactError, lambda: read_shape(_file(d, "junk.shape")))
        write_model(_file(d), _model([], 0))
        _file(d, "cut.shape").write_bytes(_file(d).read_bytes()[:30])
        _raises(ArtifactError, lambda: read_model(_file(d, "cut.shape")))
        body = b'{"$float": "banana"}'
        manifest = {"format": "shape", "format_version": 2, "shape_content_id": sha256(body)}
        write_artifact(_file(d, "tag.shape"), manifest, {"shape.json": body})
        _raises(ArtifactError, lambda: read_model(_file(d, "tag.shape")))
        write_artifact(_file(d, "nocomp.shape"), {"format": "shape", "format_version": 2}, {})
        _raises(ArtifactError, lambda: read_model(_file(d, "nocomp.shape")))


@requirement("SH2-014")
def _sh2_014() -> None:
    from shape.artifact import read_model, write_model

    model = _model(
        [
            {
                "name": "x",
                "kind": "float",
                "min": float("-inf"),
                "max": float("inf"),
                "mean": float("nan"),
                "top": [(1.5, 2), (float("nan"), 1)],
            }
        ],
        3,
    )
    with tempfile.TemporaryDirectory() as d:
        write_model(_file(d), model)
        _, back = read_model(_file(d))
    assert _same(back, model)


@requirement("SH2-015")
def _sh2_015() -> None:
    from shape.artifact import write_shape
    from shape.security import SecurityError

    with tempfile.TemporaryDirectory() as d:
        _raises(
            SecurityError, lambda: write_shape(_file(d), {"rows": 1, "token": "ghp_" + "a" * 40})
        )
        _raises(
            SecurityError,
            lambda: write_shape(_file(d), {"rows": 1}, metadata={"note": "Bearer " + "A" * 50}),
        )


@requirement("SH2-016")
def _sh2_016() -> None:
    from shape.artifact import write_model

    model = _model([{"name": "a", "mean": 1.5}], 10)
    with tempfile.TemporaryDirectory() as d:
        first = write_model(_file(d), model)
        assert write_model(_file(d, "b.shape"), json.loads(json.dumps(model))) == first


# ---------------------------------------------------------- capabilities and classification


@requirement("SH2-017")
def _sh2_017() -> None:
    from shape.spec import check_capabilities

    verdict = check_capabilities({"mandatory_capabilities": ["core/1", "future/9"]})
    assert not verdict.compatible and verdict.unknown_mandatory == ("future/9",)
    assert check_capabilities({"optional_capabilities": ["future/9"]}).compatible


@requirement("SH2-018")
def _sh2_018() -> None:
    from shape.security import SecurityError, require_no_downgrade

    _raises(SecurityError, lambda: require_no_downgrade("SENSITIVE", "PUBLIC"))
    assert require_no_downgrade("PUBLIC", "SENSITIVE")


# ------------------------------------------------------------- contracts and compatibility


@requirement("SH2-019")
def _sh2_019() -> None:
    from shape.contracts import evaluate_contract

    def unique_report(distinct: int) -> Any:
        col = {"name": "id", "distinct": float(distinct), "distinct_exact": True}
        return evaluate_contract(_model([col], 100), {"columns": {"id": {"unique": True}}})

    assert unique_report(100).passed
    bad = unique_report(99)
    assert not bad.passed and bad.violations[0].code == "unique"


@requirement("SH2-020")
def _sh2_020() -> None:
    from shape.contracts import evaluate_contract

    rel = 0.01
    models = {"cardinality": {"algorithm": "hll", "exact": False, "relative_error": rel}}
    for est in (99.4, 100.0, 100.9):  # within 1% of 100: may be unique
        col = {"name": "id", "distinct": est, "distinct_exact": False, "error_models": models}
        rep = evaluate_contract(_model([col], 100), {"columns": {"id": {"unique": True}}})
        assert rep.passed, est
    col = {"name": "id", "distinct": 90.0, "distinct_exact": False, "error_models": models}
    assert not evaluate_contract(_model([col], 100), {"columns": {"id": {"unique": True}}}).passed


@requirement("SH2-021")
def _sh2_021() -> None:
    from shape.contracts import evaluate_contract
    from shape.spec.view import null_rate

    assert null_rate({"null_count": 0}, 0) == 0.0 and null_rate({"null_count": 3}, 0) == 0.0
    col = {"name": "a", "null_count": 0}
    rep = evaluate_contract(_model([col], 0), {"columns": {"a": {"nullable_max": 0}}})
    assert rep.passed


@requirement("SH2-022")
def _sh2_022() -> None:
    from shape.contracts import compatibility

    a = _model([{"name": "x"}], 1)
    b = _model([{"name": "x"}, {"name": "y"}], 1)
    assert compatibility(a, b, "backward").compatible


@requirement("SH2-023")
def _sh2_023() -> None:
    from shape.contracts import compatibility

    a = _model([{"name": "x"}, {"name": "y"}], 1)
    b = _model([{"name": "x"}], 1)
    rep = compatibility(a, b, "backward")
    assert not rep.compatible and rep.issues[0].kind == "removed"


@requirement("SH2-024")
def _sh2_024() -> None:
    from shape.contracts import compatibility

    a = _model([{"name": "x", "kind": "int"}], 1)
    same_family = _model([{"name": "x", "kind": "float"}], 1)
    changed = _model([{"name": "x", "kind": "text"}], 1)
    assert compatibility(a, same_family, "full").compatible
    rep = compatibility(a, changed, "full")
    assert not rep.compatible and rep.issues[0].kind == "type_changed"


# -------------------------------------------------------------------------------------- drift


@requirement("SH2-025")
def _sh2_025() -> None:
    from shape.drift import compare

    a = _model([{"name": "x"}, {"name": "gone"}], 10)
    b = _model([{"name": "x"}, {"name": "new"}], 10)
    scores = {d.path: d.score for d in compare(a, b)}
    assert scores["columns.gone"] == 1 and scores["columns.new"] == 1


# ------------------------------------------------------------------------------------- query


def _with_relationships() -> dict[str, Any]:
    model = _model([{"name": "x"}, {"name": "y"}], 4)
    model["relationships"] = [
        {"kind": "correlation", "source": "x", "target": "y", "rho": 0.8},
        {"kind": "foreign_key", "source": "y", "target": "z"},
    ]
    return model


@requirement("SH2-026")
def _sh2_026() -> None:
    from shape.query import query

    model = _with_relationships()
    assert query(model, 'relationship("x","y").rho') == 0.8
    assert query(model, 'relationship("y","x").rho') == 0.8  # correlations are symmetric
    assert query(model, 'relationship("y","z").kind') == "foreign_key"
    assert query(model, 'relationship("z","y")') is None  # a foreign key is not


@requirement("SH2-027")
def _sh2_027() -> None:
    from shape.query import query

    model = _with_relationships()
    assert query(model, 'relationship("x","x")') is None
    assert query(model, 'relationship("y","y")') is None


@requirement("SH2-028")
def _sh2_028() -> None:
    from shape.query import ShapeQueryError, query

    for text in ("nonsense", 'column("x").a-b', "rows; drop", 'relationship("a")', "column()"):
        _raises(ShapeQueryError, lambda text=text: query(_model([{"name": "x"}], 1), text))


@requirement("SH2-029")
def _sh2_029() -> None:
    import os

    from shape.query import ShapeQueryError, query

    marker = Path(tempfile.gettempdir()) / "shape-conformance-must-not-exist"
    marker.unlink(missing_ok=True)
    hostile = f'__import__("os").system("touch {marker}")'
    _raises(ShapeQueryError, lambda: query(_model([], 0), hostile))
    assert not marker.exists() and os.path.basename(str(marker))


# ------------------------------------------------------------------------------ conformance


@requirement("SH2-030")
def _sh2_030() -> None:
    from dataclasses import asdict

    results = run({"passes": lambda: None, "fails": lambda: _raises(ValueError, lambda: None)})
    wire = json.loads(json.dumps([asdict(r) for r in results]))
    assert [r["name"] for r in wire] == ["passes", "fails"]
    assert wire[0]["passed"] is True and wire[1]["passed"] is False and wire[1]["detail"]


@requirement("SH2-031")
def _sh2_031() -> None:
    assert coverage_problems() == []

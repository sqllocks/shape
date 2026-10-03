"""The public Python API surface: ``shape.__all__``, its docstrings, signatures and types.

``docs/API.md`` is the reference for this surface; these tests keep it and the code in step.
"""

from __future__ import annotations

import inspect
import re
import typing
from pathlib import Path

import pyarrow as pa
import pytest

import shape
import shape.api as api
import shape.types as shape_types

ROOT = Path(__file__).resolve().parents[2]
API_DOC = ROOT / "docs" / "API.md"
PACKAGE = Path(shape.__file__).resolve().parent

# The callable packages (shape.profile, shape.query, shape.diff) forward to these functions.
_TARGETS = {name: getattr(api, name) for name in ("profile", "query", "diff")}


def _target(name: str) -> object:
    """The object behind ``shape.<name>``: the API function for the callable packages."""
    return _TARGETS.get(name) or getattr(shape, name)


def _bare_signature(obj: object) -> str:
    """``inspect.signature`` without annotations, as ``docs/API.md`` writes it."""
    sig = inspect.signature(obj)  # type: ignore[arg-type]
    params = [p.replace(annotation=inspect.Parameter.empty) for p in sig.parameters.values()]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


def _documented_signatures() -> dict[str, str]:
    """``{name: "(args)"}`` for every ``shape.name(args)`` line in the fenced blocks of API.md."""
    text = API_DOC.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for block in re.findall(r"```python\n(.*?)```", text, flags=re.S):
        for line in block.splitlines():
            m = re.match(r"^shape\.([A-Za-z_]+)(\(.*\))$", line.strip())
            if m:
                found[m.group(1)] = m.group(2)
    return found


# --- exports ---------------------------------------------------------------------


def test_every_exported_name_resolves():
    for name in shape.__all__:
        assert getattr(shape, name) is not None, name


def test_type_checking_imports_match_all():
    # The TYPE_CHECKING block is what type checkers see; it must name exactly __all__.
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    block = source.split("if TYPE_CHECKING:", 1)[1].split("\n\n", 1)[0]
    typed = set(re.findall(r" as (\w+)$", block, flags=re.M))
    assert typed == set(shape.__all__)


def test_py_typed_and_kernel_stub_ship_with_the_package():
    assert (PACKAGE / "py.typed").is_file()
    assert (PACKAGE / "_kernel.pyi").is_file()


# --- docstrings ------------------------------------------------------------------

# Defined in shape.api and shape.types, or re-exported by shape.api. The model and security
# classes (Evidence, Provenance, Shape, Sensitivity) are tracked in issue #258.
_DOCUMENTED = (
    "profile",
    "save",
    "load",
    "check",
    "diff",
    "generate",
    "timeline",
    "view",
    "query",
    "certify",
    "plan",
    "LogicalType",
    "FieldType",
    "from_arrow_type",
    "schema_from_arrow",
)


@pytest.mark.parametrize("name", _DOCUMENTED)
def test_public_name_has_a_docstring(name):
    obj = _target(name)
    doc = inspect.getdoc(obj) or ""
    # A dataclass without a docstring gets its generated signature as __doc__.
    assert doc and not doc.startswith(f"{name}("), f"shape.{name} has no docstring"


def test_errors_module_documents_every_error():
    import shape.errors as errors

    for obj in vars(errors).values():
        if inspect.isclass(obj) and issubclass(obj, Exception):
            assert inspect.getdoc(obj), obj.__name__


# --- signatures ------------------------------------------------------------------


def test_api_reference_lists_every_exported_name():
    documented = _documented_signatures()
    missing = [n for n in shape.__all__ if n not in documented and n != "Provenance"]
    assert not missing, f"docs/API.md has no signature line for {missing}"
    assert "shape.Provenance" in API_DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", [n for n in shape.__all__ if n != "Provenance"])
def test_api_reference_signature_matches_code(name):
    documented = _documented_signatures().get(name)
    assert documented == _bare_signature(_target(name)), name


def test_section_12_2_arguments_are_keyword_only():
    kw = inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(api.profile).parameters["name"].kind is kw
    assert inspect.signature(api.diff).parameters["thresholds"].kind is kw
    for name in ("scale", "mode"):
        assert inspect.signature(api.generate).parameters[name].kind is kw
    with pytest.raises(TypeError):
        api.profile(pa.table({"a": [1]}), "t")  # type: ignore[misc]


def test_return_annotations_name_the_returned_class():
    from shape.generation.fidelity import FidelityCertificate, ReconstructionPlan
    from shape.generation.timeline import ShapeTimeline
    from shape.query import ShapeView

    expected = {
        "view": ShapeView,
        "timeline": ShapeTimeline,
        "certify": FidelityCertificate,
        "plan": ReconstructionPlan,
    }
    # The classes are imported under TYPE_CHECKING (api.py stays cheap to import), so resolve
    # the annotations against them; mypy (below) checks that the imports are right.
    names = {cls.__name__: cls for cls in expected.values()}
    for name, cls in expected.items():
        assert typing.get_type_hints(getattr(api, name), localns=names)["return"] is cls, name


# --- documented behaviour --------------------------------------------------------


def _evidence() -> dict[str, typing.Any]:
    return {
        "rows": 4,
        "columns": {
            "x": {"kind": "numeric", "mean": 10.0, "variance_population": 4.0},
            "c": {"kind": "categorical", "topk": [["a", 3], ["b", 1]]},
        },
    }


def test_generate_from_evidence_returns_columns_and_report():
    from shape.generation.compiler import GenerationReport

    cols, report = api.generate(_evidence(), 5)
    assert isinstance(report, GenerationReport)
    assert set(cols) == {"x", "c"} and all(len(v) == 5 for v in cols.values())
    # seed defaults to 0 for this form
    again, _ = api.generate(_evidence(), 5, seed=0)
    assert list(cols["x"]) == list(again["x"])


def test_generate_from_profile_returns_generation_result():
    from shape.generation.engine import GenerationResult

    p = api.profile(pa.table({"id": [1, 2, 3]}), name="t")
    result = api.generate(p, 4, seed=1)
    assert isinstance(result, GenerationResult)
    assert result.tables["t"].num_rows == 4


def test_generate_from_shape_model_is_not_supported():
    # docs/API.md: the Shape model classes are not generation input (issue #251).
    model = shape.ShapeBuilder().finalize()
    with pytest.raises(AttributeError):
        api.generate(model, 3)


def test_plan_accepts_a_profile_and_an_evidence_document():
    from shape.generation.fidelity import ReconstructionPlan

    p = api.profile(pa.table({"id": [1, 2, 3]}), name="t")
    assert isinstance(api.plan(p), ReconstructionPlan)
    assert isinstance(api.plan(_evidence()), ReconstructionPlan)


def test_certify_compares_evidence_documents():
    from shape.generation.fidelity import FidelityCertificate

    cert = api.certify(_evidence(), _evidence())
    assert isinstance(cert, FidelityCertificate)


def test_timeline_requires_versions():
    with pytest.raises(ValueError, match="timeline requires versions"):
        api.timeline([])


def test_logical_type_validation_raises_value_error():
    # As documented on LogicalType (issue #260 tracks ShapeTypeError and kind validation).
    with pytest.raises(ValueError, match="decimal requires precision and scale"):
        shape_types.LogicalType("decimal")
    with pytest.raises(ValueError, match="map requires key_type and value_type"):
        shape_types.LogicalType("map", value_type=shape_types.LogicalType("string"))


def test_schema_from_arrow_keeps_names_types_and_nullability():
    schema = pa.schema([pa.field("a", pa.int32(), nullable=False), pa.field("b", pa.string())])
    fields = shape_types.schema_from_arrow(schema)
    assert fields == (
        shape_types.FieldType("a", shape_types.LogicalType("int", bit_width=32), False),
        shape_types.FieldType("b", shape_types.LogicalType("string"), True),
    )


# --- types -----------------------------------------------------------------------


def test_public_modules_pass_mypy_strict(tmp_path):
    """``shape/__init__.py``, ``api.py``, ``types.py`` and ``errors.py`` under ``--strict``
    with no ratchet entry (``shape.types`` is still on the pyproject ratchet: issue #257)."""
    from mypy import api as mypy_api

    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\npython_version = 3.11\nstrict = True\nfollow_imports = silent\n"
        f"mypy_path = {ROOT / 'src'}\ncache_dir = {tmp_path / 'cache'}\n",
        encoding="utf-8",
    )
    files = [str(PACKAGE / f) for f in ("__init__.py", "api.py", "types.py", "errors.py")]
    out, err, status = mypy_api.run(["--config-file", str(config), *files])
    assert status == 0, out + err


# --- the remaining dispatch paths of shape.api -------------------------------------


def _schema_doc(rows: int = 7) -> dict[str, typing.Any]:
    def col(name: str, strategy: str, type_: str, **gen: typing.Any) -> dict[str, typing.Any]:
        return {"name": name, "type": type_, "generator": {"strategy": strategy, **gen}}

    return {
        "schema_version": 1,
        "model": {"name": "m", "seed": 3},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": col("id", "sequence", "integer", start=1),
                    "v": col("v", "distribution", "float", low=0.0, high=1.0),
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "small", "scales": {"small": {"t": rows}}},
    }


def test_generate_from_schema_dict_and_gen_schema():
    from shape.generation.engine import GenerationResult
    from shape.generation.schema import GenSchema

    result = api.generate(_schema_doc(), seed=9)
    assert isinstance(result, GenerationResult)
    assert result.tables["t"].num_rows == 7
    again = api.generate(GenSchema.from_dict(_schema_doc()), seed=9)
    assert again.tables["t"].equals(result.tables["t"])
    # seed defaults to the schema's
    default = api.generate(_schema_doc()).tables["t"]
    assert default.equals(api.generate(_schema_doc(), seed=3).tables["t"])


def test_generate_accepts_profile_documents():
    from shape.generation.engine import GenerationResult

    one = api.profile(pa.table({"id": [1, 2, 3]}), name="t")
    assert isinstance(api.generate(one.to_dict(), 2, seed=1), GenerationResult)
    both = api.profile({"a": pa.table({"id": [1, 2]}), "b": pa.table({"k": [1, 1]})})
    result = api.generate(both.to_dict(), seed=1)
    assert isinstance(result, GenerationResult) and set(result.tables) == {"a", "b"}


def test_generate_unknown_domain_raises_shape_error():
    from shape.errors import ShapeError

    with pytest.raises(ShapeError):
        api.generate("no-such-domain-anywhere")


_EVIDENCE_MODEL = {
    "rows": 10,
    "columns": {"email": {"kind": "text", "null_count": 2, "classification": "PII"}},
}


def test_query_and_view_read_a_model_document():
    from shape.query import ShapeQueryError

    assert api.query(_EVIDENCE_MODEL, 'column("email").null_count') == 2
    assert api.query(_EVIDENCE_MODEL, 'column("missing")') is None
    view = api.view(_EVIDENCE_MODEL)
    assert view.query("rows") == 10
    assert view.classification("email") == "PII"
    with pytest.raises(ShapeQueryError):
        api.query(_EVIDENCE_MODEL, "1 + 1")


def test_query_and_view_on_a_profile_raise_model_error():
    from shape.spec.model import ModelError

    p = api.profile(pa.table({"id": [1]}), name="t")
    with pytest.raises(ModelError):
        api.query(p, "rows")
    view = api.view(p)  # read on the first call
    with pytest.raises(ModelError):
        view.query("rows")

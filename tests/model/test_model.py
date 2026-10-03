import pytest

from shape import Evidence, FieldType, LogicalType, Provenance, Sensitivity, ShapeBuilder


def test_shape_is_immutable_after_finalize():
    shape = ShapeBuilder().add_field(FieldType("x", LogicalType("int", bit_width=64))).finalize()
    with pytest.raises(AttributeError):
        shape.fields = ()


def test_evidence_mapping_is_immutable():
    shape = ShapeBuilder().add_evidence("row_count", Evidence(Provenance.OBSERVED, 10)).finalize()
    with pytest.raises(TypeError):
        shape.evidence["x"] = Evidence(Provenance.INFERRED, 1)


def test_all_provenance_classes_exist():
    assert {p.value for p in Provenance} == {
        "OBSERVED",
        "INFERRED",
        "DECLARED",
        "DERIVED",
        "INTERPOLATED",
        "EXTRAPOLATED",
    }


def test_sensitivity_join_is_conservative():
    a = Sensitivity(classifications=frozenset({"SECRET"}), categories=frozenset({"PII"}))
    b = Sensitivity(compartments=frozenset({"ALPHA"}), restrictions=frozenset({"NOFORN"}))
    c = a.join(b)
    assert c.dominates(a)
    assert c.dominates(b)


def test_join_sensitivity_accumulates_every_label():
    a = Sensitivity(classifications=frozenset({"PII"}))
    b = Sensitivity(categories=frozenset({"health"}))
    shape = ShapeBuilder().join_sensitivity(a).join_sensitivity(b).finalize()
    assert shape.sensitivity.dominates(a) and shape.sensitivity.dominates(b)
    assert shape.sensitivity == a.join(b)

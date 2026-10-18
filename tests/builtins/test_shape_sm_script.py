"""``fabric_update_shape_sm.py`` reads the Direct Lake expression name as TMDL writes it."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "fabric_update_shape_sm.py"
_spec = importlib.util.spec_from_file_location("fabric_update_shape_sm", _path)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)

PARTITION = "\tpartition t = entity\n\t\tmode: directLake\n\t\tsource\n\t\t\texpressionSource: {}\n"


def test_a_quoted_name_with_spaces_is_read_whole():
    # Fabric's Direct Lake on OneLake model (run 38366307500) names it 'DirectLake - shape_lh'
    got = sm.expression_source({"t.tmdl": PARTITION.format("'DirectLake - shape_lh'")})
    assert got == "'DirectLake - shape_lh'"


def test_a_plain_name_and_no_partition():
    assert sm.expression_source({"t.tmdl": PARTITION.format("DatabaseQuery")}) == "DatabaseQuery"
    assert sm.expression_source({"model.tmdl": "model Model\n"}) == ""


def test_the_generated_table_uses_the_quoted_name_unbroken():
    text = sm.table_tmdl(
        "customer", "dbo", [("customer_id", "int", None, None, None)], "'DirectLake - shape_lh'"
    )
    assert "expressionSource: 'DirectLake - shape_lh'\n" in text

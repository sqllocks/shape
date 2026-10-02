"""The plugin targets the plugin API major version this Shape provides, and every entry point
loads through the host and passes the conformance checks for its group."""

import shape_dbt

from shape.plugins import kit


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_dbt) == "1.0"


def test_the_sink_conforms(tmp_path):
    import pyarrow as pa

    kit.check_sink(
        shape_dbt.DbtSeedsSink(),
        uri=f"dbt://{tmp_path}",
        batches=[pa.RecordBatch.from_pydict({"id": [1, 2, 3], "name": ["a", "b", "c"]})],
    )


def test_the_commands_conform(tmp_path):
    for cmd in (shape_dbt.FromDbt(), shape_dbt.ToDbtTests(), shape_dbt.DbtSeeds(), shape_dbt.DbtReport()):
        kit.check_common(cmd, "shape.commands")

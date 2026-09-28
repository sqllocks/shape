from shape.integrations import (
    adf_activity,
    airflow_task,
    dagster_op,
    dbt_test_manifest,
    fabric_activity,
    flink_merge,
    spark_profile_partitions,
)


def test_orchestrator_and_manifests():
    t = airflow_task([{"name": "id", "fn": lambda x: x}])
    assert t([{"x": 1}]).rows == [{"x": 1}]
    assert dagster_op([{"name": "id", "fn": lambda x: x}])([{"x": 1}]).evidence
    assert dbt_test_manifest("c.json", "s.shape")["test_metadata"]["name"] == "shape_contract"
    assert adf_activity("shape", "shape check")["type"] == "Custom"
    assert fabric_activity("shape", "shape check")["type"] == "Script"


def test_spark_flink_contract():
    parts = [[{"x": 1}, {"x": 2}], [{"x": 3}]]
    s = spark_profile_partitions(parts, 2)
    assert s["rows"] == 3
    assert flink_merge([s, s])["rows"] == 6

"""Dependency-light integration contracts for common ETL/orchestration systems."""

from __future__ import annotations

from shape.distributed import DistributedProfiler
from shape.etl import ShapeETL


def airflow_task(stages):
    def task(rows):
        p = ShapeETL()
        for x in stages:
            p.stage(**x)
        return p.run(rows)

    return task


def dagster_op(stages):
    return airflow_task(stages)


def dbt_test_manifest(contract_path, shape_path):
    return {
        "name": "shape_contract",
        "test_metadata": {
            "name": "shape_contract",
            "kwargs": {"contract": contract_path, "shape": shape_path},
        },
    }


def adf_activity(name, command):
    return {"name": name, "type": "Custom", "typeProperties": {"command": command}}


def fabric_activity(name, command):
    return {
        "name": name,
        "type": "Script",
        "typeProperties": {"scripts": [{"type": "Query", "text": command}]},
    }


def spark_profile_partitions(partitions, workers=4):
    return DistributedProfiler(workers).profile(partitions)


def flink_merge(shapes):
    from shape.distributed import merge_shapes

    return merge_shapes(shapes)

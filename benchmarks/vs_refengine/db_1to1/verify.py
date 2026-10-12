"""T-22 database parity against recorded fake and SQL Server 2022 profiles."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location(
    "profile_db_recorded_comparison", HERE.parent / "profile_1to1" / "verify.py"
)
assert spec and spec.loader
profile_verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile_verify)
from recorded_store import RecordingError, load  # noqa: E402 (harness path bootstrap)


def load_testing() -> Any:
    from shape_sqlserver import testing

    return testing


CASES: dict[str, tuple[str, int, dict[str, Any], tuple[str, ...]]] = {
    "retail": ("retail", 1, {}, ()),
    "retail_no_sample": ("retail", 1, {"sample_rows": 0}, ()),
    "retail_small_sample": ("retail", 1, {"sample_rows": 100}, ()),
    "retail_subset": ("retail", 1, {"tables": ["orders", "customer"]}, ()),
    "retail_unreadable": ("retail", 1, {}, ("product",)),
    "warehouse": ("warehouse", 1, {}, ()),
    "warehouse_empty": ("warehouse_empty", 1, {}, ()),
    "name_only": ("name_only", 1, {}, ()),
    "id_named": ("id_named", 1, {}, ()),
    "retail_one_row_sample": ("retail", 1, {"sample_rows": 1}, ()),
    "mixed_keys": ("mixed_keys", 1, {}, ()),
    "key_names": ("key_names", 1, {}, ()),
}


def load_real(mssql: str, scenario: str, scale: int) -> None:
    import contextlib

    import pyodbc  # type: ignore[import-not-found]

    pyodbc.pooling = False  # a pooled session would keep its locks after close
    testing = load_testing()
    fake = testing.scenario(scenario, scale)
    schema = f"parity_{scenario}"
    with contextlib.closing(pyodbc.connect(mssql, autocommit=True)) as conn:
        cur = conn.cursor()
        drop_real(cur, fake, schema)
        cur.execute(f"CREATE SCHEMA [{schema}]")
        for stmt in testing.ddl(fake, schema):
            cur.execute(stmt)
        for table in fake.tables:
            testing.insert_rows(cur, table, schema)


def drop_real(cur: Any, fake: Any, schema: str) -> None:
    for fk in fake.foreign_keys:
        cur.execute(
            f"IF OBJECT_ID('[{schema}].[{fk.child_table}]') IS NOT NULL "
            f"ALTER TABLE [{schema}].[{fk.child_table}] DROP CONSTRAINT IF EXISTS [{fk.name}]"
        )
    for table in reversed(fake.tables):
        cur.execute(f"DROP TABLE IF EXISTS [{schema}].[{table.name}]")
    cur.execute(f"IF SCHEMA_ID('{schema}') IS NOT NULL DROP SCHEMA [{schema}]")


def scenario_table_digests(scenario: str, scale: int = 1) -> dict[str, str]:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.repro import dataset_id

    conn = load_testing().scenario(scenario, scale)
    return {
        "scenario/" + scenario + "/" + t.name: dataset_id(
            {
                t.name: pa.Table.from_arrays(
                    [pa.array([row[i] for row in t.rows]) for i, c in enumerate(t.columns)],
                    names=[c.name for c in t.columns],
                )
            }
        )
        for t in conn.tables
    }


def scenario_inputs(scenario: str, scale: int = 1) -> dict[str, str]:
    return {
        name: hashlib.sha256(identifier.encode()).hexdigest()
        for name, identifier in scenario_table_digests(scenario, scale).items()
    }


def shape_side(
    scenario: str, scale: int, kw: dict[str, Any], fail: tuple[Any, ...], mssql: str | None
) -> dict[str, Any]:
    from shape_sqlserver import Credentials, profile_database

    if mssql:
        profile = profile_database(
            mssql, credentials=Credentials("sql"), schema="parity_" + scenario, **kw
        )
    else:
        conn = load_testing().scenario(scenario, scale)
        conn.fail_reads = set(fail)
        profile = profile_database(connection=conn, **kw)
    return cast(dict[str, Any], profile_verify.sd.normalize(profile))


def compare_profiles(
    base: dict[str, Any],
    candidate: dict[str, Any],
    label: str,
    matrix: dict[str, Any],
    failures: list[Any],
    sampled: Any = None,
) -> None:
    profile_verify.compare_profile(base, candidate, label, matrix, failures)


def baseline_profile(case: str, mssql: bool = False) -> dict[str, Any]:
    scenario, scale, _, _ = CASES[case]
    identifier = case + (":mssql" if mssql else "")
    return cast(
        dict[str, Any],
        profile_verify.sd.restore_order(
            load("db", identifier, inputs=scenario_inputs(scenario, scale))["profile"]
        ),
    )


def cleanup_real(connection: str, scenario: str, scale: int) -> None:
    import pyodbc

    with contextlib.closing(pyodbc.connect(connection, autocommit=True)) as conn:
        drop_real(conn.cursor(), load_testing().scenario(scenario, scale), "parity_" + scenario)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", action="append", choices=list(CASES))
    ap.add_argument("--mssql")
    a = ap.parse_args(argv)
    failed = 0
    loaded = set()
    for case in a.case or list(CASES):
        try:
            base = baseline_profile(case, bool(a.mssql))
        except RecordingError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        scenario, scale, kw, fail = CASES[case]
        try:
            if a.mssql and scenario not in loaded:
                load_real(a.mssql, scenario, scale)
                loaded.add(scenario)
            candidate = shape_side(scenario, scale, kw, fail, a.mssql)
            matrix: dict[str, Any] = {}
            errors: list[str] = []
            compare_profiles(base, candidate, case, matrix, errors)
            print(case + ": " + ("PASS" if not errors else str(len(errors)) + " mismatches"))
            for err in errors:
                print("MISMATCH", err)
            failed += len(errors)
        except Exception:
            print("db:" + case + ": candidate database profiling failed", file=sys.stderr)
            return 2
        finally:
            if a.mssql and scenario in loaded:
                try:
                    cleanup_real(a.mssql, scenario, scale)
                except Exception:
                    print("db:" + case + ": database cleanup failed", file=sys.stderr)
                    return 2
                loaded.remove(scenario)
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())

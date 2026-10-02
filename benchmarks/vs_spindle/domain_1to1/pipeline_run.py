"""Write what the pipeline generation artifacts produce as one run directory, for ``verify.py``.

    source scripts/env.sh
    P=benchmarks/vs_spindle/domain_1to1/pipeline_run.py
    BENCH_OUT_DIR=$HOME/bench-out-pf06 "$FABRIC_VENV/bin/python" $P --path notebook --seed 1042
    BENCH_OUT_DIR=$HOME/bench-out-pf06 "$FABRIC_VENV/bin/python" $P --path udf --seed 1042
    BENCH_OUT_DIR=$HOME/bench-out-pf06 "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py \\
        --domain retail --scale small --impl shape

The run goes to ``$BENCH_OUT_DIR/shape/<domain>/<scale>/seed<N>/`` (the layout ``generate.py``
writes), so use a ``BENCH_OUT_DIR`` of its own: it replaces a run of the product path there.

* ``notebook``: executes the code cells of ``integrations/fabric/notebooks/shape_generate.ipynb`` against
  a scratch directory standing in for the lakehouse (Delta tables written with ``deltalake``), reads
  the Delta tables back and writes them as Parquet. Delta stores timestamps as microseconds, so the
  tables read back carry ``timestamp[us]`` where the engine's own columns are ``timestamp[ns]``; by
  default each such column is cast back to the unit the engine declares (the values are the Delta
  ones). ``--delta-types`` keeps what Delta stores, which makes clause (a) of T-21 (identical Arrow
  types) fail on those columns; the report says which.
* ``udf``: calls ``shape.integrations.fabric.udf.generate_sample`` (the code behind ``generateSample``)
  once per table with ``rows`` equal to the table's count at the scale, and writes each DataFrame as
  Parquet. A DataFrame has pandas types (``str`` for strings, no ``large_string``), which the verifier
  normalizes; timestamps come back as ``timestamp[us]`` and are cast to the engine's unit as above.

Needs the ``fabric-demo`` environment: ``pip install -e '.[dev]' -r tests/demo/fabric/requirements.txt``
(``deltalake``, ``nbformat``) and the domains plugin. Exit codes: 0 ok, 2 unsupported domain.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR  # noqa: E402

SUCCESS = "_SUCCESS"
NOTEBOOK = HERE.parents[2] / "integrations" / "fabric" / "notebooks" / "shape_generate.ipynb"
LAKEHOUSE = "/lakehouse/default"


class _NotebookExit(BaseException):
    """The real ``notebookutils.notebook.exit`` ends the notebook; so does the stand-in."""

    def __init__(self, value: str):
        super().__init__(value)
        self.value = value


def _install_notebookutils() -> None:
    module = types.ModuleType("notebookutils")

    def _exit(value: str = "") -> None:
        raise _NotebookExit(value)

    module.notebook = types.SimpleNamespace(exit=_exit)  # type: ignore[attr-defined]
    sys.modules["notebookutils"] = module


def run_notebook(lakehouse: Path, params: dict) -> dict:
    """Execute the notebook's code cells (magics skipped) with ``params``; return its exit value."""
    import nbformat

    _install_notebookutils()
    namespace: dict = {"__name__": "__main__"}
    for cell in nbformat.read(NOTEBOOK, as_version=4).cells:
        if cell.cell_type != "code" or cell.source.lstrip().startswith("%%"):
            continue
        source = "\n".join(ln for ln in cell.source.splitlines() if not ln.lstrip().startswith("%"))
        try:
            exec(
                compile(source.replace(LAKEHOUSE, str(lakehouse)), NOTEBOOK.name, "exec"), namespace
            )
        except _NotebookExit as done:
            return json.loads(done.value)
        if "parameters" in cell.metadata.get("tags", []):
            namespace.update(params)  # what Fabric injects after the parameters cell
    raise RuntimeError("the notebook never called notebookutils.notebook.exit")


def _declared_types(domain: str, scale: str, seed: int):
    """The Arrow schema of every table as the engine declares it."""
    from shape.integrations.fabric import generation

    result = generation.generate_domain(domain, scale=scale, seed=seed)
    return result, {name: table.schema for name, table in result.tables.items()}


def _restore_units(table, declared):
    """``table`` with each timestamp column cast to the unit ``declared`` gives it."""
    import pyarrow as pa

    for i, field in enumerate(declared):
        have = table.schema.field(field.name).type
        if pa.types.is_timestamp(field.type) and pa.types.is_timestamp(have) and have != field.type:
            table = table.set_column(i, field.name, table[field.name].cast(field.type))
    return table


def _write(dest: Path, tables: dict, order: list[str], meta: dict) -> None:
    import pyarrow.parquet as pq

    for name in order:
        pq.write_table(tables[name], dest / f"{name}.parquet", compression="snappy")
    (dest / SUCCESS).write_text(
        json.dumps({**meta, "rows": {name: tables[name].num_rows for name in order}})
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", choices=["notebook", "udf"], required=True)
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--scale", default="small")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument(
        "--delta-types",
        action="store_true",
        help="keep the types Delta stores (timestamp[us]) instead of the engine's declared ones",
    )
    a = ap.parse_args(argv)

    from shape.generation.domains import domain_names

    if a.domain not in domain_names():
        print(f"unsupported: no domain {a.domain!r} (installed: {domain_names()})", file=sys.stderr)
        return 2
    final = BENCH_OUT_DIR / "shape" / a.domain / a.scale / f"seed{a.seed}"
    tmp = final.with_name(final.name + ".pf06tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    result, declared = _declared_types(a.domain, a.scale, a.seed)
    # the order the engine generated the tables in (dependency levels): what generate.py records
    order = list(result.tables)
    meta = {"impl": "shape", "domain": a.domain, "scale": a.scale, "seed": a.seed, "via": a.path}

    tables = {}
    if a.path == "notebook":
        import deltalake

        with tempfile.TemporaryDirectory() as scratch:
            lake = Path(scratch) / "lakehouse"
            (lake / "Tables").mkdir(parents=True)
            (lake / "Files").mkdir()
            out = run_notebook(
                lake,
                {
                    "domain": a.domain,
                    "scale": a.scale,
                    "seed": a.seed,
                    "mode": "",
                    "tablePrefix": "",
                    "writeMode": "overwrite",
                    "outputDir": "shape",
                },
            )
            for entry in out["tables"]:
                delta = deltalake.DeltaTable(str(lake / "Tables" / entry["deltaTable"]))
                tables[entry["table"]] = delta.to_pyarrow_table()
        assert set(order) == {entry["table"] for entry in out["tables"]}
    else:
        import pyarrow as pa

        from shape.integrations.fabric import udf

        for name in order:
            frame = udf.generate_sample(
                a.domain, name, rows=result.tables[name].num_rows, seed=a.seed
            )
            tables[name] = pa.Table.from_pandas(frame, schema=None, preserve_index=False)
    if not a.delta_types:
        tables = {name: _restore_units(t, declared[name]) for name, t in tables.items()}
    _write(tmp, tables, order, meta)
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    print(f"wrote {final} via {a.path} ({sum(t.num_rows for t in tables.values()):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

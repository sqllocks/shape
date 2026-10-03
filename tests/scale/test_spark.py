"""P6-13: the fabric_spark router and the Spark worker, as contract tests (recorded Fabric
interactions and a stand-in Spark session; live runs need the owner's O-02 secrets)."""

from __future__ import annotations

import base64
import json

import pyarrow as pa
import pytest
from fakes import LH, NB, RUN, WS, FakeFabric
from scale_schemas import computed_doc, plain_doc

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.scale import spark_worker
from shape.scale.api import submit_spark
from shape.scale.jobs import Jobs, JobStore
from shape.scale.notebooks import worker_notebook
from shape.scale.spark import FabricSparkRouter, build_spec, default_requirements

TOKEN = "fabric-token-abc"
STORAGE = "storage-token-xyz"
SPEC = build_spec(
    plain_doc(), seed=7, row_counts={"customer": 40}, chunk_rows=500, sinks=["lakehouse"]
)


def router(fake, **kw):
    return FabricSparkRouter(
        WS, LH, TOKEN, storage_token=STORAGE, transport=fake, sleep=lambda s: None,
        requirements=["sqllocks-shape==0.9.0"], **kw,
    )  # fmt: skip


# ---- the router -----------------------------------------------------------------------------


def test_submit_uploads_the_spec_finds_the_notebook_and_starts_the_run():
    fake = FakeFabric()
    run = router(fake, table_prefix="demo_").submit(SPEC)
    steps = [
        (m, u.replace(WS, "WS").replace(LH, "LH").replace(NB, "NB")) for m, u in fake.methods()
    ]
    assert steps[0] == (
        "PUT",
        f"https://onelake.dfs.fabric.microsoft.com/WS/LH/Files/shape_jobs/{run.run_id}.json",
    )
    assert [m for m, _ in steps] == ["PUT", "PATCH", "PATCH", "GET", "POST"]
    assert steps[3][1].endswith("/workspaces/WS/notebooks")
    assert steps[4][1].endswith("/workspaces/WS/items/NB/jobs/instances")
    assert run.notebook_item_id == NB and run.fabric_run_id.startswith(RUN[:-1])
    assert run.spec_path == f"shape_jobs/{run.run_id}.json"
    (uploaded,) = fake.uploaded_specs()
    assert uploaded == SPEC
    start = json.loads(fake.calls[-1]["body"])
    assert start["executionData"]["configuration"]["conf"] == {
        "spark.shape.jobSpec": run.spec_path,
        "spark.shape.tablePrefix": "demo_",
    }
    assert "jobType=RunNotebook" in fake.calls[-1]["url"]


def test_tokens_go_in_headers_only_and_each_call_uses_its_audience():
    fake = FakeFabric()
    router(fake).submit(SPEC)
    for call in fake.calls:
        assert TOKEN not in call["url"] and STORAGE not in call["url"]
        expected = STORAGE if "onelake" in call["url"] else TOKEN
        assert call["headers"]["Authorization"] == f"Bearer {expected}"


def test_a_big_spec_is_appended_in_order_in_one_megabyte_pieces():
    fake = FakeFabric()
    big = dict(SPEC, filler="x" * (2_500_000))
    router(fake).upload_spec(big, "r1")
    appends = [c for c in fake.calls if "action=append" in c["url"]]
    assert [len(c["body"]) for c in appends[:2]] == [1024 * 1024, 1024 * 1024]
    assert json.loads(bytes(next(iter(fake.files.values())))) == big


def test_a_missing_notebook_is_created_from_the_bundled_template():
    fake = FakeFabric(has_notebook=False)
    r = router(fake, notebook_name="my_worker")
    assert r.get_or_create_notebook() == NB
    body = fake.created
    assert body["displayName"] == "my_worker" and body["type"] == "Notebook"
    parts = {
        p["path"]: base64.b64decode(p["payload"]).decode() for p in body["definition"]["parts"]
    }
    assert set(parts) == {"notebook-content.ipynb", ".platform"}
    nb = json.loads(parts["notebook-content.ipynb"])
    text = json.dumps(nb)
    assert WS in text and LH in text and "__SHAPE_" not in text
    assert "sqllocks-shape==0.9.0" in text
    assert json.loads(parts[".platform"])["metadata"]["displayName"] == "my_worker"


def test_an_asynchronous_notebook_creation_is_polled_until_it_is_done():
    fake = FakeFabric(has_notebook=False, async_create=True)
    assert router(fake).get_or_create_notebook() == NB
    assert ("GET", "https://api.fabric.microsoft.com/v1/operations/op-1") in fake.methods()


def test_an_existing_notebook_is_reused():
    fake = FakeFabric()
    router(fake).get_or_create_notebook()
    assert not [c for c in fake.calls if c["method"] == "POST"]


@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"workspace_id": "not-a-guid"}, "workspace_id must be a GUID"),
        ({"lakehouse_id": "../x"}, "lakehouse_id must be a GUID"),
        ({"token": ""}, "needs a Fabric token"),
        ({"notebook_name": "a/b"}, "invalid notebook name"),
        ({"table_prefix": "a-b"}, "letters, digits"),
        ({"requirements": ["x; rm -rf /"]}, "plain pip requirement"),
    ],
)
def test_router_rejects_bad_settings(kwargs, text):
    args = {"workspace_id": WS, "lakehouse_id": LH, "token": TOKEN, **kwargs}
    with pytest.raises(ValueError, match=text):
        FabricSparkRouter(
            args.pop("workspace_id"), args.pop("lakehouse_id"), args.pop("token"), **args
        )


def test_default_requirements_pin_this_version():
    reqs = default_requirements()
    assert reqs[0].startswith("sqllocks-shape==") and reqs[1].startswith("sqllocks-shape-domains==")


def test_a_run_with_no_id_in_the_response_is_an_error():
    fake = FakeFabric()
    r = router(fake)
    original = fake.__call__

    def no_location(method, url, headers, body, timeout):
        resp = original(method, url, headers, body, timeout)
        if url.endswith("jobs/instances?jobType=RunNotebook"):
            resp.headers.clear()
        return resp

    r._http._transport = no_location
    with pytest.raises(RuntimeError, match="no run id"):
        r.submit_run(NB, "p")


def test_submit_spark_end_to_end_registers_a_job_without_secrets(tmp_path):
    fake = FakeFabric()
    store = JobStore(tmp_path / "jobs")
    request = {
        "domain": "retail", "scale": "small", "scale_mode": "fabric_spark", "sinks": ["lakehouse"],
        "sink_config": {"lakehouse": {"base_path": "x", "client_secret": "s3"}}, "chunk_size": 500,
        "fabric": {"workspace_id": WS, "lakehouse_id": LH, "table_prefix": "t_"},
    }  # fmt: skip
    out = submit_spark(request, TOKEN, jobs=Jobs(store), storage_token=STORAGE, transport=fake)
    assert out["status"] == "submitted" and out["total_rows_queued"] == 21750
    assert out["fabric"]["notebook_item_id"] == NB and out["spec_path"].startswith("shape_jobs/")
    saved = (tmp_path / "jobs" / f"{out['job_id']}.json").read_text()
    assert TOKEN not in saved and STORAGE not in saved and "s3" not in saved
    (spec,) = fake.uploaded_specs()
    assert spec["row_counts"]["order_line"] == 12500 and spec["table_prefix"] == "t_"
    # a failed run is submitted again under the same job id
    store.update(out["job_id"], status="failed")
    resumed = Jobs(store, transport=fake).resume_spark(
        out["job_id"], TOKEN, lambda req: submit_spark(
            req, TOKEN, jobs=None, storage_token=STORAGE, transport=fake)["fabric"],
    )  # fmt: skip
    assert resumed["job_id"] == out["job_id"] and resumed["attempts"] == 2


def test_submit_spark_needs_ids_and_a_token():
    with pytest.raises(ValueError, match="needs fabric.workspace_id"):
        submit_spark(
            {
                "domain": "retail",
                "scale_mode": "fabric_spark",
                "chunk_size": 5,
                "sinks": ["lakehouse"],
                "sink_config": {},
            },
            TOKEN,
            jobs=None,
        )


# ---- the notebook ---------------------------------------------------------------------------


def test_the_notebook_is_valid_nbformat_with_the_placeholders_the_router_fills():
    nb = worker_notebook()
    assert nb["nbformat"] == 4 and len(nb["cells"]) == 5
    text = json.dumps(nb)
    for placeholder in (
        "__SHAPE_WORKSPACE_ID__",
        "__SHAPE_LAKEHOUSE_ID__",
        "__SHAPE_REQUIREMENTS__",
    ):
        assert placeholder in text
    assert "spindle" not in text.lower()
    for cell in nb["cells"]:
        compile("".join(cell["source"]), "<cell>", "exec")  # every cell is valid Python
    assert "run_job" in text and "check_result" in text and "spark.shape.jobSpec" in text


# ---- the worker, against a stand-in Spark ---------------------------------------------------


class FakeWriter:
    def __init__(self, sink):
        self.sink, self.fmt, self.opts, self.path = sink, None, {}, None

    def format(self, fmt):
        self.fmt = fmt
        return self

    def mode(self, m):
        self.opts["mode"] = m
        return self

    def option(self, k, v):
        self.opts[k] = v
        return self

    def save(self, path):
        self.sink.saved[path] = (self.fmt, dict(self.opts), self.frame)


class FakeFrame:
    def __init__(self, sink, table=None, mapper=None):
        self._sink, self.table, self.mapper = sink, table, mapper

    @property
    def write(self):
        w = FakeWriter(self._sink)
        w.frame = self
        return w

    def mapInArrow(self, fn, schema):
        return FakeFrame(self._sink, None, (fn, schema, self.table))


class FakeSpark:
    """Runs the executors' function in this process, one partition per chunk number."""

    def __init__(self):
        self.saved = {}

        class Ctx:
            def broadcast(self_, value):  # noqa: N805
                return type("B", (), {"value": value})()

        self.sparkContext = Ctx()

    def range(self, start, end, step, parts):
        ids = pa.RecordBatch.from_pydict({"id": list(range(start, end))})
        return FakeFrame(self, ids)

    def createDataFrame(self, pdf):  # noqa: N802
        return FakeFrame(self, pa.Table.from_pandas(pdf))

    @property
    def read(self):
        """``spark.read.format("delta").load(path).count()``: the rows saved at ``path``."""
        spark = self

        class Reader:
            def format(self, fmt):
                assert fmt == "delta"
                return self

            def load(self, path):
                frame = spark.saved[path][2]
                return type("Loaded", (), {"count": lambda _: collect(frame).num_rows})()

        return Reader()


def collect(frame) -> pa.Table:
    if frame.mapper is None:
        return frame.table
    fn, _, ids = frame.mapper
    batches = []
    for index in ids.column(0).to_pylist():  # one partition per chunk, as Spark makes them
        batches += list(fn(iter([pa.RecordBatch.from_pydict({"id": [index]})])))
    return pa.Table.from_batches(batches)


def run_worker(doc, rows, chunk_rows):
    spec = build_spec(
        doc, seed=9, row_counts=rows, chunk_rows=chunk_rows, sinks=["lakehouse"], table_prefix="p_"
    )
    spark = FakeSpark()
    messages = []
    result = spark_worker.run_job(
        spark, spark_worker.load_spec(json.dumps(spec)), workspace_id=WS, lakehouse_id=LH,
        table_prefix="p_", log=messages.append,
    )  # fmt: skip
    return spec, spark, result, messages


ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


def test_large_tables_are_made_by_the_executors_chunk_by_chunk_and_equal_the_engine():
    spec, spark, result, messages = run_worker(plain_doc(ROWS), ROWS, 500)
    assert result["distributed"] == ["order", "order_line"]
    assert result["tables"] == ROWS and result["rows_generated"] == sum(ROWS.values())
    spark_worker.check_result(spec, result)
    root = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables"
    assert set(spark.saved) == {f"{root}/p_{t}" for t in ROWS}
    fmt, opts, _ = spark.saved[f"{root}/p_order"]
    assert fmt == "delta" and opts == {"mode": "overwrite", "overwriteSchema": "true"}
    engine = Engine(GenSchema.from_dict(plain_doc(ROWS)), seed=9)
    direct = engine.generate()
    for table in ("order", "order_line"):
        made = collect(spark.saved[f"{root}/p_{table}"][2])
        assert made.num_rows == ROWS[table]
        assert made.combine_chunks().equals(direct.tables[table].combine_chunks()), table
    assert any("made on the executors" in m for m in messages)


def test_tables_a_post_pass_changes_are_made_on_the_driver_and_still_equal_the_engine():
    doc = computed_doc(ROWS)
    spec, spark, result, _ = run_worker(doc, ROWS, 500)
    assert result["distributed"] == ["order_line"]  # order has a computed column
    spark_worker.check_result(spec, result)
    root = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables"
    direct = Engine(GenSchema.from_dict(doc), seed=9).generate()
    order = collect(spark.saved[f"{root}/p_order"][2])
    assert order.num_rows == 1200 and "total" in order.column_names
    assert order["total"].to_pylist() == pytest.approx(direct.tables["order"]["total"].to_pylist())


def test_nothing_is_distributed_when_no_table_is_larger_than_a_chunk():
    spec, spark, result, _ = run_worker(plain_doc(ROWS), ROWS, 10_000)
    assert result["distributed"] == [] and result["tables"] == ROWS
    spark_worker.check_result(spec, result)


def test_check_result_fails_when_the_counts_differ():
    spec = build_spec(plain_doc(ROWS), seed=1, row_counts=ROWS, chunk_rows=10, sinks=[])
    with pytest.raises(RuntimeError, match="row counts differ"):
        spark_worker.check_result(spec, {"tables": {**ROWS, "order": 5}})


def test_load_spec_rejects_anything_else():
    for bad in ("[]", '{"version": 2, "schema": {}}', '{"version": 1}'):
        with pytest.raises(ValueError, match="job spec"):
            spark_worker.load_spec(bad)


def test_arrow_to_ddl_maps_the_engine_types_and_refuses_others():
    schema = pa.schema(
        [("a", pa.int64()), ("b", pa.float64()), ("c", pa.string()), ("d", pa.bool_()),
         ("e", pa.date32()), ("f", pa.timestamp("us")), ("g", pa.decimal128(10, 2))]
    )  # fmt: skip
    assert spark_worker.arrow_to_ddl(schema) == (
        "`a` bigint, `b` double, `c` string, `d` boolean, `e` date, `f` timestamp, "
        "`g` decimal(10,2)"
    )
    with pytest.raises(TypeError, match="no Spark type"):
        spark_worker.arrow_to_ddl(pa.schema([("x", pa.list_(pa.int64()))]))


def test_the_driver_does_not_regenerate_the_tables_the_executors_make(monkeypatch):
    # Regression #484: engine.generate() on the driver made order and order_line in full too.
    from collections import Counter

    made: Counter[str] = Counter()
    on_executor = [False]
    original = Engine.generate_chunk
    executor_fn = spark_worker._chunk_batches

    def spy(self, table, start, n_rows, chunk=0):
        if not on_executor[0]:
            made[table] += n_rows
        return original(self, table, start, n_rows, chunk=chunk)

    def executor(spec_json, table):
        run = executor_fn(spec_json, table)

        def flagged(batches):
            on_executor[0] = True
            try:
                yield from run(batches)
            finally:
                on_executor[0] = False

        return flagged

    monkeypatch.setattr(Engine, "generate_chunk", spy)
    monkeypatch.setattr(spark_worker, "_chunk_batches", executor)
    spec, spark, result, _ = run_worker(plain_doc(ROWS), ROWS, 500)
    assert result["distributed"] == ["order", "order_line"]
    assert +made == Counter(customer=40)  # zero-row schema samples drop out
    spark_worker.check_result(spec, result)
    root = f"abfss://{WS}@onelake.dfs.fabric.microsoft.com/{LH}/Tables"
    direct = Engine(GenSchema.from_dict(plain_doc(ROWS)), seed=9).generate()
    driver_made = collect(spark.saved[f"{root}/p_customer"][2])
    assert driver_made.to_pylist() == direct.tables["customer"].to_pylist()


def test_the_written_row_count_of_an_executor_table_is_measured(monkeypatch):
    # Regression #485: run_job reported the spec's count for executor-made tables.
    lost = []

    def lose_a_chunk(spec_json, table):
        whole = original(spec_json, table)

        def run(batches):
            for batch in whole(batches):
                if table == "order" and not lost:
                    lost.append(batch.num_rows)  # the first chunk made is never written
                    continue
                yield batch

        return run

    original = spark_worker._chunk_batches
    monkeypatch.setattr(spark_worker, "_chunk_batches", lose_a_chunk)
    spec, _, result, _ = run_worker(plain_doc(ROWS), ROWS, 500)
    assert result["tables"]["order"] == 700  # 1200 asked, the first 500-row chunk lost
    with pytest.raises(RuntimeError, match="row counts differ"):
        spark_worker.check_result(spec, result)

"""Oracle sink acceptance and boundary tests (W9-15)."""

import datetime as dt
import decimal

import pyarrow as pa
import pytest
from shape_databases.errors import WriteError
from shape_databases.oracle import OracleSink
from shape_databases.oracle_testing import FakeOracle
from shape_databases.oracle_types import oracle_type

from shape.errors import ShapeError


@pytest.mark.parametrize(
    "kind,expected",
    [
        (pa.int8(), "NUMBER(3)"),
        (pa.int16(), "NUMBER(5)"),
        (pa.int32(), "NUMBER(10)"),
        (pa.int64(), "NUMBER(19)"),
        (pa.uint8(), "NUMBER(5)"),
        (pa.uint16(), "NUMBER(10)"),
        (pa.uint32(), "NUMBER(19)"),
        (pa.uint64(), "NUMBER(20)"),
        (pa.float32(), "BINARY_FLOAT"),
        (pa.float64(), "BINARY_DOUBLE"),
        (pa.decimal128(12, 4), "NUMBER(12,4)"),
        (pa.bool_(), "NUMBER(1)"),
        (pa.string(), "CLOB"),
        (pa.binary(), "BLOB"),
        (pa.binary(7), "RAW(7)"),
        (pa.date32(), "DATE"),
        (pa.time32("s"), "INTERVAL DAY(0) TO SECOND(0)"),
        (pa.time64("ns"), "INTERVAL DAY(0) TO SECOND(9)"),
        (pa.timestamp("ms"), "TIMESTAMP(3)"),
        (pa.timestamp("us", "Europe/London"), "TIMESTAMP(6) WITH TIME ZONE"),
        (pa.struct([("a", pa.int64())]), "CLOB"),
        (pa.list_(pa.int64()), "CLOB"),
        (pa.map_(pa.string(), pa.int64()), "CLOB"),
    ],
)
def test_oracle_arrow_type_map(kind, expected):
    assert oracle_type(pa.field("value", kind)) == expected


def test_oracle_types_version_and_width_boundaries():
    field = pa.field("value", pa.string())
    assert oracle_type(pa.field("flag", pa.bool_()), server_major=23) == "BOOLEAN"
    assert oracle_type(field, {"max_length": 4000}) == "VARCHAR2(4000 CHAR)"
    assert oracle_type(field, {"max_length": 4001}) == "CLOB"
    assert oracle_type(field, {"max_length": 32767}, max_string_size=32767) == (
        "VARCHAR2(32767 CHAR)"
    )
    assert oracle_type(field, {"max_length": 32768}, max_string_size=32767) == "CLOB"
    assert oracle_type(pa.field("raw", pa.binary()), {"max_length": 2000}) == "RAW(2000)"
    assert oracle_type(pa.field("raw", pa.binary()), {"max_length": 2001}) == "BLOB"
    assert oracle_type(pa.field("uuid", pa.string(), metadata={b"shape.type": b"uuid"})) == (
        "VARCHAR2(36 CHAR)"
    )
    assert oracle_type(pa.field("v", pa.decimal128(38, 10))) == "NUMBER(38,10)"
    with pytest.raises(ShapeError, match="v.*38"):
        oracle_type(pa.field("v", pa.decimal256(39, 4)))
    with pytest.raises(ShapeError, match="v.*precision"):
        oracle_type(pa.field("v", pa.float64()), {"type": "decimal"})


@pytest.mark.parametrize("name", ["x" * 129, "é" * 65, "bad\x00name"])
def test_oracle_names_are_refused_before_connection(name):
    fake = FakeOracle()
    with pytest.raises(ShapeError):
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service", name, [pa.record_batch({"id": [1]})]
        )
    assert fake.connections == []


def test_oracle_quoted_case_array_dml_and_server_checks():
    fake = FakeOracle(version="23.5.0.0.0", max_string_size="EXTENDED")
    sink = OracleSink(connect=fake.connect)
    batch = pa.record_batch({"Id": [1, 2, 3], "Flag": [True, False, None]})
    assert (
        sink.write(
            "oracle://shape@localhost:1521/service?schema=CaseSchema",
            "CaseTable",
            [batch],
            batch_rows=2,
            primary_key=["Id"],
        )
        == 3
    )
    assert '"CaseSchema"."CaseTable"' in fake.ddl[0]
    assert '"Flag" BOOLEAN' in fake.ddl[0]
    assert [len(rows) for _, rows in fake.dml] == [2, 1]
    assert len(fake.input_sizes) == 1
    assert fake.version_reads == 1
    assert fake.parameter_reads == 1


def test_oracle_19_boolean_check_and_older_server_refusal():
    fake = FakeOracle(version="19.22.0.0.0")
    OracleSink(connect=fake.connect).write(
        "oracle://shape@localhost/service", "t", [pa.record_batch({"flag": [True]})]
    )
    assert 'CHECK ("flag" IN (0,1))' in fake.ddl[0]
    old = FakeOracle(version="18.0.0.0.0")
    with pytest.raises(ShapeError, match="18.*19"):
        OracleSink(connect=old.connect).write(
            "oracle://shape@localhost/service", "t", [pa.record_batch({"id": [1]})]
        )
    assert old.ddl == [] and old.dml == []


@pytest.mark.parametrize("coerce", [False, True])
def test_oracle_empty_strings_refuse_or_count_nullable_coercion(coerce, caplog):
    fake = FakeOracle()
    sink = OracleSink(connect=fake.connect)
    batch = pa.record_batch({"text": ["", "kept", None]})
    if coerce:
        assert (
            sink.write("oracle://shape@localhost/service", "words", [batch], empty_string="null")
            == 3
        )
        assert fake.dml[0][1] == [(None,), ("kept",), (None,)]
        assert "1 empty string" in caplog.text
    else:
        with pytest.raises(ShapeError, match="words.*text.*empty.string"):
            sink.write("oracle://shape@localhost/service", "words", [batch])
        assert fake.dml == []


def test_oracle_not_null_empty_string_never_coerces():
    fake = FakeOracle()
    with pytest.raises(ShapeError, match="words.*text.*NOT NULL"):
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service",
            "words",
            [pa.record_batch({"text": [""]})],
            empty_string="null",
            columns={"text": {"nullable": False}},
        )
    assert fake.dml == []


def test_oracle_passwords_and_wallets_never_appear_in_errors(monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD", "fixture password with spaces")
    fake = FakeOracle(fail_connect="fixture password with spaces")
    with pytest.raises(WriteError) as failure:
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service", "t", [pa.record_batch({"id": [1]})]
        )
    assert "fixture password" not in str(failure.value)
    for uri in (
        "oracle://shape:fixture@localhost/service",
        "oracle://shape@localhost/service?password=fixture",
    ):
        blocked = FakeOracle()
        with pytest.raises(ShapeError, match="password.*URI"):
            OracleSink(connect=blocked.connect).write(uri, "t", [pa.record_batch({"id": [1]})])
        assert blocked.connections == []


def test_oracle_tls_wallet_reference_and_literal_wallet_password_refusal(monkeypatch):
    monkeypatch.setenv("WALLET_TEST_PASSWORD", "fixture wallet pass")
    fake = FakeOracle()
    OracleSink(connect=fake.connect).write(
        "oracle://shape@db.example/service?ssl=true",
        "t",
        [pa.record_batch({"id": [1]})],
        wallet_location="/fixture/wallet",
        wallet_password="env://WALLET_TEST_PASSWORD",
    )
    params = fake.connections[0]
    assert params["protocol"] == "tcps" and params["ssl_server_dn_match"] is True
    assert params["wallet_password"] == "fixture wallet pass"
    assert params["wallet_location"] == "/fixture/wallet"
    with pytest.raises(ShapeError, match="wallet_password.*reference"):
        OracleSink(connect=fake.connect).plan(
            "oracle://shape@localhost/service", "t", {"wallet_password": "literal"}
        )


def test_oracle_temporal_decimal_values_are_bound_not_interpolated():
    fake = FakeOracle()
    schema = pa.schema(
        [
            ("amount", pa.decimal128(12, 4)),
            ("at", pa.timestamp("us", "UTC")),
            ("time", pa.time64("us")),
        ]
    )
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array([decimal.Decimal("12.3400")], type=schema[0].type),
            pa.array(
                [dt.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=dt.UTC)], type=schema[1].type
            ),
            pa.array([dt.time(3, 4, 5, 123456)], type=schema[2].type),
        ],
        schema=schema,
    )
    OracleSink(connect=fake.connect).write("oracle://shape@localhost/service", "t", [batch])
    statement, rows = fake.dml[0]
    assert "TO_TIMESTAMP_TZ(:2" in statement and "TO_DSINTERVAL(:3)" in statement
    assert "12.3400" not in statement and "2026-01-02" not in statement
    assert rows[0][0] == decimal.Decimal("12.3400")
    assert "123456" in rows[0][1] and "+00:00" in rows[0][1]


def test_oracle_commit_rows_reports_only_earlier_committed_rows():
    fake = FakeOracle(fail_dml_call=2)
    with pytest.raises(WriteError) as failure:
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service",
            "t",
            [pa.record_batch({"id": [1, 2, 3, 4]})],
            batch_rows=2,
            commit_rows=2,
        )
    assert failure.value.rows_committed == 2
    assert fake.rollbacks == 1


def test_oracle_tcps_scheme_cannot_downgrade_tls():
    fake = FakeOracle()
    sink = OracleSink(connect=fake.connect)
    sink.write("tcps://shape@localhost/service", "t", [pa.record_batch({"id": [1]})])
    assert fake.connections[0]["protocol"] == "tcps"
    with pytest.raises(ShapeError, match="tcps.*ssl"):
        sink.plan("tcps://shape@localhost/service?ssl=false", "t", {})


def test_oracle_nanoseconds_and_null_first_batch_keep_declared_binds():
    fake = FakeOracle()
    schema = pa.schema([("at", pa.timestamp("ns", "UTC")), ("time", pa.time64("ns"))])
    empty = pa.RecordBatch.from_arrays(
        [pa.array([None], type=f.type) for f in schema], schema=schema
    )
    precise = pa.RecordBatch.from_arrays(
        [pa.array([1_234_567_891], type=f.type) for f in schema], schema=schema
    )
    OracleSink(connect=fake.connect).write(
        "oracle://shape@localhost/service", "t", [empty, precise]
    )
    assert fake.dml[0][1] == [(None, None)]
    assert "234567891" in fake.dml[1][1][0][0]
    assert "234567891" in fake.dml[1][1][0][1]
    assert fake.input_sizes == [(64, 64)]


@pytest.mark.parametrize(
    "unit,value,expected",
    [("s", 1, "+0 00:00:01.000000000"), ("ms", 1234, "+0 00:00:01.234000000")],
)
def test_oracle_time32_bound_values(unit, value, expected):
    fake = FakeOracle()
    batch = pa.record_batch([pa.array([value], type=pa.time32(unit))], names=["time"])
    OracleSink(connect=fake.connect).write("oracle://shape@localhost/service", "t", [batch])
    assert fake.dml[0][1] == [(expected,)]


def test_oracle_dictionary_normalization_preserves_uuid_and_not_null():
    schema = pa.schema(
        [
            pa.field("id", pa.string(), nullable=False, metadata={b"shape.type": b"uuid"}),
            pa.field("category", pa.dictionary(pa.int8(), pa.string()), nullable=False),
        ]
    )
    batch = pa.RecordBatch.from_arrays(
        [
            pa.array(["d6b7ad00-9f1a-4a76-9e9d-464b9e3cfe63"]),
            pa.array(["a"]).dictionary_encode().cast(schema.field("category").type),
        ],
        schema=schema,
    )
    fake = FakeOracle()
    OracleSink(connect=fake.connect).write("oracle://shape@localhost/service", "t", [batch])
    assert '"id" VARCHAR2(36 CHAR) NOT NULL' in fake.ddl[0]
    assert '"category" CLOB NOT NULL' in fake.ddl[0]


def test_oracle_later_batch_type_change_is_refused_before_second_dml():
    fake = FakeOracle()
    with pytest.raises(ShapeError, match="type"):
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service",
            "t",
            [pa.record_batch({"value": [1]}), pa.record_batch({"value": [1.5]})],
        )
    assert len(fake.dml) == 1


def test_oracle_registered_generate_route_uses_bound_values(monkeypatch, tmp_path, capsys):
    import json
    import sys
    from types import SimpleNamespace

    from shape import generate
    from shape.cli.main import main
    from shape.generation.schema import Column, Generation, GenSchema, Model, Table

    schema = GenSchema(
        Model(name="oracle", seed=7),
        {
            "CaseTable": Table(
                "CaseTable",
                {
                    "id": Column("id", "string", {"strategy": "uuid"}, max_length=36),
                    "value": Column("value", "float", {"strategy": "uniform", "low": 0, "high": 1}),
                },
                ["id"],
            )
        },
        generation=Generation(scales={"small": {"CaseTable": 11}}),
    )
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema.to_dict()))
    fake = FakeOracle()
    monkeypatch.setitem(
        sys.modules,
        "oracledb",
        SimpleNamespace(
            connect=fake.connect,
            is_thin_mode=lambda: True,
            DB_TYPE_BINARY_DOUBLE="DB_TYPE_BINARY_DOUBLE",
        ),
    )
    uri = "oracle://shape@localhost/service"
    assert main(["generate", str(path), "--to", uri, "--seed", "7", "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["targets"][uri] == {"CaseTable": 11}
    assert fake.input_sizes == [(36, "DB_TYPE_BINARY_DOUBLE")]
    expected = generate(schema, seed=7)["CaseTable"]
    assert [row for _, rows in fake.dml for row in rows] == list(
        zip(expected.column("id").to_pylist(), expected.column("value").to_pylist(), strict=True)
    )


def test_oracle_driver_failure_never_persists_password(monkeypatch, tmp_path):
    import json

    from shape.bridge.core import Bridge

    secret = "oracle-private-password"
    monkeypatch.setenv("ORACLE_PASSWORD", secret)
    fake = FakeOracle(fail_connect=f"connection failed {secret}")
    monkeypatch.setattr(OracleSink, "default_connect", lambda self, **kw: fake.connect(**kw))
    bridge = Bridge(tmp_path / "jobs")
    try:
        result = bridge.handle(
            {
                "api_version": "1.2",
                "command": "profile",
                "args": {"source": "oracle://shape@localhost/service", "sample_rows": 1},
            }
        )
        assert not result["ok"] and secret not in json.dumps(result)
        for record in (tmp_path / "jobs").rglob("*.json"):
            assert secret not in record.read_text()
    finally:
        bridge.close()


@pytest.mark.parametrize("later_type", [False, True])
def test_oracle_late_validation_reports_committed_rows(later_type):
    fake = FakeOracle()
    batches = (
        [pa.record_batch({"value": [1]}), pa.record_batch({"value": [1.5]})]
        if later_type
        else [pa.record_batch({"text": ["kept", ""]})]
    )
    with pytest.raises(WriteError, match="1 rows were committed") as error:
        OracleSink(connect=fake.connect).write(
            "oracle://shape@localhost/service", "t", batches, batch_rows=1, commit_rows=1
        )
    assert error.value.rows_committed == 1 and len(fake.dml) == 1
    assert "type" in str(error.value) if later_type else "empty-string" in str(error.value)

"""AUD-security2 #300: the Spark router's checks take whole values only (a trailing newline is
not part of a GUID, a name or a requirement), and column names stay one quoted Spark name."""

from __future__ import annotations

import pyarrow as pa
import pytest
from fakes import LH, WS

from shape.scale.spark import FabricSparkRouter, check_guid
from shape.scale.spark_worker import arrow_to_ddl


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workspace_id": WS + "\n"},
        {"lakehouse_id": LH + "\n"},
        {"notebook_name": "worker\n"},
        {"table_prefix": "gen_\n"},
        {"requirements": ["sqllocks-shape==0.9.0\n"]},
    ],
)
def test_a_trailing_newline_is_refused(kwargs):
    args = {"workspace_id": WS, "lakehouse_id": LH, "token": "t", **kwargs}
    with pytest.raises(ValueError):
        FabricSparkRouter(
            args.pop("workspace_id"), args.pop("lakehouse_id"), args.pop("token"), **args
        )


def test_check_guid_takes_the_whole_value():
    with pytest.raises(ValueError):
        check_guid(WS + "\n", "workspace_id")


def test_a_backtick_in_a_column_name_stays_inside_its_quotes():
    ddl = arrow_to_ddl(pa.schema([("a` string, `evil", pa.int64()), ("b", pa.string())]))
    assert ddl == "`a`` string, ``evil` bigint, `b` string"

"""A job record never holds a credential (HUNT2-fabric #633, #634, #635)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.scale.api import scale_generate, submit_spark
from shape.scale.jobs import MASK, Jobs, JobStateError, JobStore, _merged
from shape.scale.sinks import redact
from tests.scale.fakes import LH, WS, FakeFabric

SPN = {"mode": "spn", "tenant_id": "t", "client_id": "c", "client_secret": "SUPERSECRET-1"}


def _files(folder: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.json"))


def test_a_local_record_masks_the_secret_in_auth(tmp_path):
    scale_generate(
        {"domain": "retail", "scale": "small", "sinks": ["memory"], "auth": SPN},
        jobs=Jobs(JobStore(tmp_path)),
    )
    text = _files(tmp_path)
    assert "SUPERSECRET-1" not in text
    stored = json.loads(next(tmp_path.glob("*.json")).read_text())["request"]["auth"]
    assert stored["client_secret"] == MASK
    assert (stored["mode"], stored["tenant_id"], stored["client_id"]) == ("spn", "t", "c")


def test_a_credential_reference_in_auth_is_kept(tmp_path):
    auth = {**SPN, "client_secret": "env://SHAPE_TEST_SECRET"}
    scale_generate(
        {"domain": "retail", "scale": "small", "sinks": ["memory"], "auth": auth},
        jobs=Jobs(JobStore(tmp_path)),
    )
    stored = json.loads(next(tmp_path.glob("*.json")).read_text())["request"]["auth"]
    assert stored["client_secret"] == "env://SHAPE_TEST_SECRET"


def test_a_spark_record_masks_the_secret_in_auth(tmp_path):
    request = {
        "domain": "retail",
        "scale": "small",
        "scale_mode": "fabric_spark",
        "sinks": ["lakehouse"],
        "sink_config": {},
        "chunk_size": 500000,
        "auth": {"mode": "sql", "sql_user": "u", "sql_password": "SUPERSECRET-2"},
        "fabric": {"workspace_id": WS, "lakehouse_id": LH},
    }
    submit_spark(request, "tok", jobs=Jobs(JobStore(tmp_path)), storage_token="s", transport=FakeFabric())
    assert "SUPERSECRET-2" not in _files(tmp_path)


def test_resuming_with_a_masked_auth_asks_for_it_again():
    base = {"sink_config": {}, "auth": {"mode": "spn", "client_secret": MASK}}
    with pytest.raises(JobStateError, match="auth"):
        _merged(base, None)
    ok = _merged(base, {"auth": {"client_secret": "env://X"}})
    assert ok["auth"]["client_secret"] == "env://X"


@pytest.mark.parametrize(
    ("value", "secret"),
    [
        ("Server=x;Pwd={abc;def};Database=d", "def"),
        ("Server=x;Password='a;b';", "b'"),
        ("mssql://user:pw123@host/db", "pw123"),
        ("Server=x;Access Token=abcdef;", "abcdef"),
        ("Server=x;Password = abc;", "abc"),
    ],
)
def test_redact_hides_every_form_of_secret(value, secret):
    out = redact({"warehouse": {"connection_string": value}})["warehouse"]["connection_string"]
    assert secret not in out


def test_redact_keeps_what_is_not_secret():
    out = redact({"warehouse": {"connection_string": "Server=x;Database=d;Uid=u", "n": 3}})
    assert out == {"warehouse": {"connection_string": "Server=x;Database=d;Uid=u", "n": 3}}


@pytest.mark.parametrize(
    "value", ["Server=x;Uid=u;Pwd=***;", "mssql://user:***@host/db", "Server=x;Access Token=***;"]
)
def test_resume_notices_a_mask_inside_a_string(value):
    base = {"sink_config": {"warehouse": {"connection_string": value}}}
    with pytest.raises(JobStateError, match="warehouse"):
        _merged(base, None)


def test_resume_accepts_a_string_without_a_mask():
    base = {"sink_config": {"warehouse": {"connection_string": "Server=x;Uid=u;Database=d"}}}
    assert _merged(base, None) == base

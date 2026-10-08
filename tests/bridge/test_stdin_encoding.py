"""#539: the stdio bridge reads requests as UTF-8 whatever the locale, and a line that is not
UTF-8 is answered `usage.invalid_json`."""

from __future__ import annotations

import json
import os
import subprocess
import sys

C_LOCALE = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}


def _run(jobs_dir, payload: bytes, *extra: str) -> list[dict]:
    env = {k: v for k, v in os.environ.items() if k not in C_LOCALE}
    env.update({k: v for k, v in C_LOCALE.items() if v})
    done = subprocess.run(
        [sys.executable, "-m", "shape", "bridge", "--jobs-dir", str(jobs_dir), *extra],
        input=payload,
        capture_output=True,
        timeout=120,
        env=env,
    )
    return [json.loads(x) for x in done.stdout.decode("ascii").splitlines()]


def _list(request_id: str) -> bytes:
    request = {"api_version": "1.0", "id": request_id, "command": "list"}
    return json.dumps(request, ensure_ascii=False).encode("utf-8")


def test_a_non_ascii_request_is_read_as_utf8_under_the_c_locale(tmp_path):
    (doc,) = _run(tmp_path / "jobs", _list("café-1") + b"\n")
    assert doc["ok"] and doc["id"] == "café-1", doc["id"]
    (doc,) = _run(tmp_path / "jobs", _list("café-2"), "--once")
    assert doc["ok"] and doc["id"] == "café-2", doc["id"]


def test_a_line_that_is_not_utf8_is_invalid_json_and_the_bridge_goes_on(tmp_path):
    good = b'{"api_version": "1.0", "id": 2, "command": "list"}'
    docs = _run(tmp_path / "jobs", b'{"id": 1, "command": "l\xffst"}\n' + good + b"\n")
    assert [d["ok"] for d in docs] == [False, True]
    assert docs[0]["error"]["code"] == "usage.invalid_json"
    assert "UTF-8" in docs[0]["error"]["message"]

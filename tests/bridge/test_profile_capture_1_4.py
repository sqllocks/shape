"""SHAPE-BRIDGE-001: versioned capture preserves bridge privacy and real job semantics."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

import shape
from shape.bridge.core import Bridge


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "private.csv"
    path.write_text(
        "id,email,status\n" + "".join(f"{n},private{n}@example.com,active\n" for n in range(40)),
        encoding="utf-8",
    )
    return path


def call(
    bridge: Bridge,
    command: str = "profile",
    *,
    options: dict[str, Any] | None = None,
    version: str = "1.4",
    **args: Any,
) -> dict[str, Any]:
    return bridge.handle(
        {
            "api_version": version,
            "id": "capture",
            "command": command,
            "args": args,
            "options": options or {},
        }
    )


def artifact(path: Path) -> tuple[dict[str, Any], str]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    return manifest, text


@pytest.mark.parametrize("capture", ["safe", "full", None])
@pytest.mark.parametrize("explicit_output", [False, True])
def test_sync_capture_persists_real_artifact(
    bridge: Bridge, source: Path, tmp_path: Path, capture: str | None, explicit_output: bool
) -> None:
    args: dict[str, Any] = {"source": str(source)}
    if capture is not None:
        args["capture"] = capture
    if explicit_output:
        args["output"] = str(tmp_path / "requested.shape")
    response = call(bridge, **args)
    assert response["ok"], response
    result = response["result"]
    path = Path(result["path"])
    manifest, text = artifact(path)
    mode = capture or "full"
    assert manifest["capture"]["mode"] == mode
    assert shape.load(path).capture["mode"] == mode
    assert manifest["shape_content_id"] == result["content_id"]
    expected = shape.save(shape.profile(str(source)), tmp_path / "expected.shape", capture=mode)
    assert result["content_id"] == expected
    assert ("private0@example.com" in text) is (mode == "full")
    warnings = [warning["code"] for warning in response["warnings"]]
    assert ("profile_file_holds_values" in warnings) is (mode == "full")
    if explicit_output:
        assert path == tmp_path / "requested.shape"
    else:
        assert path.name == result["content_id"].replace(":", "-") + ".shape"


@pytest.mark.parametrize("explicit_output", [False, True])
def test_async_safe_capture_has_real_job_status_file_and_content_id(
    bridge: Bridge, source: Path, tmp_path: Path, jobs_dir: Path, explicit_output: bool
) -> None:
    args: dict[str, Any] = {"source": str(source), "capture": "safe"}
    if explicit_output:
        args["output"] = str(tmp_path / "async.shape")
    response = call(bridge, options={"async": True}, **args)
    assert response["ok"], response
    job_id = response["result"]["job_id"]
    assert isinstance(job_id, str)
    deadline = time.monotonic() + 30
    while True:
        status = call(bridge, "job_status", job_id=job_id)
        assert status["ok"], status
        job = status["result"]
        if job["status"] not in ("running", "submitted"):
            break
        assert time.monotonic() < deadline, job
        time.sleep(0.01)
    assert job["status"] == "succeeded" and job["error"] is None
    record = json.loads((jobs_dir / "bridge" / f"{job_id}.json").read_text("utf-8"))
    assert record["request"]["args"]["capture"] == "safe"
    assert record["status"] == "succeeded" and record["result"] == job["result"]
    result = job["result"]
    manifest, text = artifact(Path(result["path"]))
    assert manifest["capture"] == {"mode": "safe", "k": 5}
    assert "private0@example.com" not in text
    expected = shape.save(shape.profile(str(source)), tmp_path / "expected.shape", capture="safe")
    assert manifest["shape_content_id"] == result["content_id"] == expected
    restarted = Bridge(jobs_dir)
    try:
        assert call(restarted, "job_status", job_id=job_id)["result"] == job
    finally:
        restarted.close()


@pytest.mark.parametrize("capture", ["invalid", "", True, 1, [], {}])
@pytest.mark.parametrize("as_job", [False, True])
def test_invalid_capture_is_refused_before_read_write_or_job(
    bridge: Bridge, tmp_path: Path, jobs_dir: Path, capture: Any, as_job: bool
) -> None:
    output = tmp_path / "must-not-exist" / "output.shape"
    response = call(
        bridge,
        source=str(tmp_path / "missing.csv"),
        output=str(output),
        capture=capture,
        options={"async": as_job},
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "usage.invalid_argument"
    assert not output.parent.exists()
    assert not list((jobs_dir / "bridge").glob("job-*.json"))
    assert bridge.jobs.list() == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_earlier_api_versions_still_refuse_capture(
    bridge: Bridge, tmp_path: Path, version: str
) -> None:
    response = call(
        bridge,
        source=str(tmp_path / "missing.csv"),
        capture="safe",
        version=version,
        options={"async": True},
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "usage.unknown_argument"
    assert "capture needs api_version 1.4" in response["error"]["hint"]
    assert bridge.jobs.list() == []


@pytest.mark.parametrize("capture", ["safe", "full"])
def test_capture_does_not_grant_raw_summary_permission(
    bridge: Bridge, source: Path, tmp_path: Path, capture: str
) -> None:
    safe = call(bridge, source=str(source), capture=capture)
    assert safe["ok"], safe
    columns = safe["result"]["summary"]["columns"]
    assert columns["email"]["min"] is None and columns["email"]["max"] is None
    assert columns["email"]["redacted"] is True
    assert "private0@example.com" not in json.dumps(safe)
    raw = call(
        bridge,
        source=str(source),
        capture=capture,
        options={"include_raw_values": True},
        output=str(tmp_path / "raw-summary.shape"),
    )
    assert raw["ok"] and raw["result"]["summary"]["columns"]["email"]["min"] is not None
    assert artifact(Path(raw["result"]["path"]))[0]["capture"]["mode"] == capture


@pytest.mark.parametrize(
    "scheme",
    ["bigquery", "postgresql", "postgres", "mysql", "snowflake", "databricks", "oracle", "tcps"],
)
def test_database_capture_stays_safe_even_when_full_is_requested(
    bridge: Bridge, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    profile = shape.profile(str(source))
    seen: list[str] = []

    def database_profile(source: str, **kwargs: Any) -> Any:
        seen.append(source)
        return profile

    monkeypatch.setattr(shape, "profile", database_profile)
    response = call(
        bridge,
        source=f"{scheme}://fixture",
        capture="full",
        output=str(tmp_path / "database.shape"),
    )
    assert response["ok"], response
    assert seen == [f"{scheme}://fixture"]
    manifest, text = artifact(Path(response["result"]["path"]))
    assert manifest["capture"]["mode"] == "safe"
    assert "private0@example.com" not in text
    assert "profile_file_holds_values" not in [warning["code"] for warning in response["warnings"]]

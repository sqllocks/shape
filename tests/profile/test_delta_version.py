"""ISS-stream (#36): profile a historical state of a Delta table (``version=`` / ``as_of=``).

The table is built with ``deltalake`` in a temp directory: version 0 holds 100 rows, version 1
appends 50 (150), version 2 overwrites with 10. Commits are 1.2 s apart so that an ``as_of``
between two commits is unambiguous whatever the file system's timestamp resolution.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta, timezone

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.profile.reference.sources import SourceError

deltalake = pytest.importorskip("deltalake")

ROWS = {0: 100, 1: 150, 2: 10}


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    path = tmp_path_factory.mktemp("delta") / "events"
    deltalake.write_deltalake(str(path), pa.table({"id": list(range(100)), "kind": ["a"] * 100}))
    time.sleep(1.2)
    deltalake.write_deltalake(
        str(path), pa.table({"id": list(range(100, 150)), "kind": ["b"] * 50}), mode="append"
    )
    time.sleep(1.2)
    deltalake.write_deltalake(
        str(path), pa.table({"id": list(range(10)), "kind": ["c"] * 10}), mode="overwrite"
    )
    return path


def _committed(table) -> dict[int, datetime]:
    history = deltalake.DeltaTable(str(table)).history()
    return {h["version"]: datetime.fromtimestamp(h["timestamp"] / 1000, UTC) for h in history}


def test_the_latest_state_is_the_default_and_its_version_is_recorded(table):
    prof = shape.profile(table)
    assert prof.to_dict()["row_count"] == ROWS[2]
    assert prof.provenance is not None
    assert prof.provenance["format"] == "delta"
    assert prof.provenance["version"] == 2
    assert prof.provenance["timestamp"] == _committed(table)[2].isoformat()
    assert prof.provenance["as_of"] is None


@pytest.mark.parametrize("version", [0, 1, 2])
def test_version_profiles_that_state(table, version):
    prof = shape.profile(table, version=version)
    assert prof.to_dict()["row_count"] == ROWS[version]
    assert prof.provenance["version"] == version
    assert prof.provenance["timestamp"] == _committed(table)[version].isoformat()


def test_the_historical_profile_is_the_data_of_that_version(table):
    prof = shape.profile(table, version=0).to_dict()
    assert list(prof["columns"]["kind"]["enum_values"]) == ["a"]
    assert prof["columns"]["id"]["max_value"] == ["int", 99]
    assert shape.profile(table, version=1).to_dict()["columns"]["kind"]["cardinality"] == 2


def test_as_of_picks_the_newest_version_committed_at_or_before(table):
    at = _committed(table)
    between = at[1] + timedelta(milliseconds=500)  # after commit 1, 700 ms before commit 2
    for when in (between, between.isoformat(), between.isoformat().replace("+00:00", "Z")):
        prof = shape.profile(table, as_of=when)
        assert prof.to_dict()["row_count"] == ROWS[1], when
        assert prof.provenance["version"] == 1
        assert prof.provenance["as_of"] == between.isoformat()
    assert shape.profile(table, as_of=at[2] + timedelta(days=1)).provenance["version"] == 2


def test_as_of_accepts_an_offset_and_reads_a_naive_time_as_utc(table):
    at = _committed(table)[1] + timedelta(milliseconds=500)
    plus_two = at.astimezone(timezone(timedelta(hours=2)))
    assert shape.profile(table, as_of=plus_two).provenance["version"] == 1
    naive = at.replace(tzinfo=None)
    prof = shape.profile(table, as_of=naive)
    assert prof.provenance["version"] == 1
    assert prof.provenance["as_of"] == at.isoformat()  # recorded in UTC


def test_as_of_before_the_first_commit_is_an_error_not_version_zero(table):
    before = _committed(table)[0] - timedelta(days=1)
    with pytest.raises(SourceError, match="no version of .* at or before"):
        shape.profile(table, as_of=before)


def test_a_version_that_does_not_exist_is_an_error(table):
    with pytest.raises(SourceError, match="version 9"):
        shape.profile(table, version=9)


@pytest.mark.parametrize("version", [-1, True, 1.5, "1"])
def test_a_bad_version_is_refused(table, version):
    with pytest.raises(ValueError, match="version"):
        shape.profile(table, version=version)


def test_a_bad_as_of_is_refused(table):
    with pytest.raises(ValueError, match="as_of"):
        shape.profile(table, as_of="last tuesday")
    with pytest.raises(ValueError, match="as_of"):
        shape.profile(table, as_of=5)


def test_version_and_as_of_together_are_refused(table):
    with pytest.raises(ValueError, match="not both"):
        shape.profile(table, version=1, as_of="2030-01-01T00:00:00Z")


def test_version_and_as_of_need_a_delta_table(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("a\n1\n")
    with pytest.raises(SourceError, match="Delta"):
        shape.profile(csv, version=0)
    with pytest.raises(SourceError, match="Delta"):
        shape.profile({"t": csv}, as_of="2030-01-01")
    with pytest.raises(SourceError, match="Delta"):
        shape.profile(pa.table({"a": [1]}), version=0)


def test_a_file_profile_has_no_provenance(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("a\n1\n")
    assert shape.profile(csv).provenance is None


def test_provenance_survives_save_and_load_and_is_not_in_the_profile_body(table, tmp_path):
    prof = shape.profile(table, version=1)
    out = tmp_path / "v1.shape"
    shape.save(prof, out, capture="full")  # equality with the in-memory profile needs full
    again = shape.load(out)
    assert again.provenance == prof.provenance
    assert again == prof
    assert "provenance" not in again.to_dict()


def test_the_content_id_is_of_the_profile_body_not_of_how_it_was_read(table, tmp_path):
    at = (_committed(table)[1] + timedelta(milliseconds=500)).isoformat()
    by_version = shape.save(shape.profile(table, version=1), tmp_path / "a.shape")
    by_time = shape.save(shape.profile(table, as_of=at), tmp_path / "b.shape")
    assert by_version == by_time


def test_diff_between_versions_sees_the_change(table):
    drift = shape.diff(shape.profile(table, version=0), shape.profile(table, version=1))
    assert drift is not None


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_version(capsys, table, tmp_path):
    out = tmp_path / "v0.shape"
    code, text, err = run(capsys, "profile", table, "--version", 0, "-o", out)
    assert code == 0, err
    doc = json.loads(text)
    assert doc["provenance"]["version"] == 0
    assert shape.load(out).to_dict()["row_count"] == ROWS[0]
    assert shape.load(out).provenance["format"] == "delta"


def test_cli_as_of(capsys, table, tmp_path):
    when = (_committed(table)[1] + timedelta(milliseconds=500)).isoformat()
    out = tmp_path / "asof.shape"
    code, text, err = run(capsys, "profile", table, "--as-of", when, "-o", out)
    assert code == 0, err
    assert json.loads(text)["provenance"]["version"] == 1
    assert shape.load(out).to_dict()["row_count"] == ROWS[1]


def test_cli_version_one_is_not_the_global_version_flag(capsys, table, tmp_path):
    code, text, _ = run(capsys, "profile", table, "--version", 1, "-o", tmp_path / "x.shape")
    assert code == 0 and json.loads(text)["provenance"]["version"] == 1
    code, text, _ = run(capsys, "--version")
    assert code == 0 and text.startswith("shape ")


def test_cli_latest_reports_the_version_it_read(capsys, table, tmp_path):
    code, text, _ = run(capsys, "profile", table, "-o", tmp_path / "x.shape")
    assert code == 0
    assert json.loads(text)["provenance"]["version"] == 2


def test_cli_refuses_both_and_a_non_delta_source(capsys, table, tmp_path):
    code, _, err = run(
        capsys, "profile", table, "--version", 1, "--as-of", "2030-01-01", "-o", tmp_path / "x"
    )
    assert code == 2 and "not both" in err
    csv = tmp_path / "t.csv"
    csv.write_text("a\n1\n")
    code, _, err = run(capsys, "profile", csv, "--version", 0, "-o", tmp_path / "x")
    assert code == 2 and "Delta" in err
    with pytest.raises(SystemExit) as exit_:
        run(capsys, "profile", table, "--version", "latest", "-o", tmp_path / "x")
    assert exit_.value.code == 2


def test_cli_inspect_shows_the_provenance(capsys, table, tmp_path):
    out = tmp_path / "v1.shape"
    run(capsys, "profile", table, "--version", 1, "-o", out)
    code, text, _ = run(capsys, "inspect", out)
    assert code == 0
    assert json.loads(text)["provenance"]["version"] == 1

"""HUNT2-scenario: regression tests for defects found in the second audit of ``shape demo``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_run_local import session_of  # noqa: E402  (the helper of the sibling module)

from shape.demo.connections import ConnectionProfile, check_profile
from shape.demo.errors import DemoError

# ---- #662: a relative local folder is recorded as an absolute path -----------------------------


def test_662_cleanup_from_another_directory_removes_a_relative_session_folder(
    run, home, tmp_path, schema_file, monkeypatch
):
    start, elsewhere = tmp_path / "start", tmp_path / "elsewhere"
    start.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(start)
    assert run("demo", "init", "--name", "rel", "--local-path", "land")[0] == 0
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "rel",
        "--domain", schema_file, "--rows", "1000", "--seed", "3",
    )  # fmt: skip
    assert code == 0, out
    session = session_of(out)
    folder = start / "land" / session
    assert folder.is_dir()
    record = json.loads((home / "sessions" / f"demo-{session}.json").read_text())
    assert all(Path(a["detail"]).is_absolute() for a in record["artifacts"])

    monkeypatch.chdir(elsewhere)
    code, out, _ = run("demo", "cleanup", session)
    assert code == 0, out
    assert "already gone" not in out
    assert not folder.exists()


# ---- #664: a profile never stores a secret in a URL, a token or a key=value part ------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("eventhouse_uri", "https://user:pw@h.kusto.windows.net"),
        ("eventhouse_uri", "https://h.kusto.windows.net/?sig=abc%3D&sv=1"),
        ("warehouse_staging_path", "https://acct.blob.core.windows.net/c?sv=1&sig=abc%3D"),
        ("warehouse_staging_path", "abfss://c@acct.dfs.core.windows.net/p?sas_token=xyz"),
        ("sql_db_conn_str", "Server=x;User ID=a;Pass=abc"),
        ("sql_db_conn_str", "Server=x;Access Token=abc"),
        ("warehouse_conn_str", "Server=x;Uid=a;Pwd=abc"),
    ],
)
def test_664_a_profile_with_a_secret_in_any_field_is_refused(field, value):
    with pytest.raises(DemoError, match="credential reference|env://"):
        check_profile(ConnectionProfile(name="a", **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("eventhouse_uri", "https://h.kusto.windows.net"),
        ("warehouse_staging_path", "abfss://c@acct.dfs.core.windows.net/staging/path"),
        ("warehouse_staging_path", "onelake://ws/lh/Files/staging"),
        (
            "sql_db_conn_str",
            "Server=tcp:x.database.windows.net;Database=d;Authentication=ActiveDirectoryDefault",
        ),
        ("eventhouse_uri", "env://EVENTHOUSE_URI"),
        ("local_path", "/tmp/a?b"),
    ],
)
def test_664_a_profile_without_a_secret_is_accepted(field, value):
    assert check_profile(ConnectionProfile(name="a", **{field: value})).name == "a"


# ---- #663: the comparison page does not print the values of a personal-data column --------------


def people_csv(path: Path) -> Path:
    import csv

    names = ["Alice", "Bob", "Carla", "Dmitri"]
    with path.open("w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["id", "email", "ssn", "plan", "city"])
        for i in range(400):
            who = names[i % 4]
            ssn = f"{100 + i % 800}-{10 + i % 80}-{1000 + i}"
            plan, city = ["basic", "plus", "pro"][i % 3], ["Springfield", "Gotham"][i % 2]
            out.writerow([i, f"{who.lower()}@secretcorp.example", ssn, plan, city])
    return path


def test_663_the_page_withholds_the_values_of_a_classified_column(tmp_path):
    from shape.demo.charts import render_html
    from shape.generation.learn import as_dataset
    from shape.profile.reference import profile

    real = as_dataset(profile(people_csv(tmp_path / "people.csv")))
    page = render_html(real, real, 1.0, "people")
    assert "secretcorp.example" not in page and "alice@" not in page
    # the shape of the column stays; the values of ordinary categories stay
    assert "<td>email</td>" in page
    assert "basic" in page and "Springfield" in page


def test_663_the_page_says_a_column_is_withheld(tmp_path):
    from shape.demo.charts import render_html
    from shape.generation.learn import as_dataset
    from shape.profile.reference import profile

    real = as_dataset(profile(people_csv(tmp_path / "people.csv")))
    assert "values withheld" in render_html(real, real, 1.0, "people")


# ---- #701: a dry run shows what the real cleanup would do ------------------------------------


def write_session(home: Path, session: str, artifacts: list[dict]) -> None:
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    record = {
        "session_id": session, "scenario": "retail", "mode": "seeding", "started_at": "t",
        "finished_at": "t", "success": True, "error": None, "artifacts": artifacts,
        "params": {}, "metrics": {}, "scale_mode": None, "fabric_run_id": None,
        "workspace_id": None, "notebook_item_id": None,
    }  # fmt: skip
    (home / "sessions" / f"demo-{session}.json").write_text(json.dumps(record))


def test_701_a_dry_run_leaves_alone_what_the_real_cleanup_leaves_alone(run, home, tmp_path):
    precious = tmp_path / "precious.txt"
    precious.write_text("keep")
    gone = tmp_path / "gone.txt"
    write_session(
        home,
        "abc12345",
        [
            {"target": "file", "name": "precious.txt", "row_count": 0, "detail": str(precious)},
            {"target": "file", "name": "gone.txt", "row_count": 0, "detail": str(gone)},
        ],
    )
    code, dry, _ = run("demo", "cleanup", "abc12345", "--dry-run")
    assert code == 0
    assert "Would remove: file/precious.txt" not in dry
    assert "Left alone: file/precious.txt (not inside a folder this session created)" in dry
    code, real, _ = run("demo", "cleanup", "abc12345")
    assert code == 0 and precious.read_text() == "keep"
    assert "Left alone: file/precious.txt (not inside a folder this session created)" in real
    assert "Left alone: file/gone.txt (already gone)" in dry and "already gone" in real


def test_701_a_dry_run_still_lists_the_session_folder_it_would_remove(
    run, home, tmp_path, schema_file
):
    from test_run_local import session_of

    assert run("demo", "init", "--name", "loc", "--local-path", tmp_path / "land")[0] == 0
    code, out, _ = run(
        "demo", "run", "retail", "--mode", "seeding", "--connection", "loc",
        "--domain", schema_file, "--rows", "1000", "--seed", "3",
    )  # fmt: skip
    session = session_of(out)
    code, dry, _ = run("demo", "cleanup", session, "--dry-run")
    assert code == 0 and "[dry-run] Would remove: file/customer" in dry
    assert (tmp_path / "land" / session / "customer").is_dir()  # nothing was removed
    code, real, _ = run("demo", "cleanup", session)
    assert "Removed: file/customer" in real and not (tmp_path / "land" / session).exists()


# ---- #716: an input file that cannot be read is a message that names the file ---------------


def test_716_a_repeated_column_name_is_a_message_without_a_traceback(run, home, tmp_path, caplog):
    path = tmp_path / "dup.csv"
    path.write_text("a,a\n1,2\n", encoding="utf-8")
    code, out, err = run("demo", "run", "retail", "--input-file", path, "--rows", "200")
    assert code == 1
    assert "Traceback" not in out + err and "inference demo failed" not in caplog.text
    assert f"{path}" in err and "more than once" in err


def test_716_a_file_that_is_not_utf8_says_so_and_names_the_file(run, home, tmp_path):
    path = tmp_path / "u16.csv"
    path.write_bytes("a\n1\n".encode("utf-16"))
    code, out, err = run("demo", "run", "retail", "--input-file", path, "--rows", "200", "--json")
    result = json.loads(out)
    assert code == 1 and result["success"] is False
    assert str(path) in result["error"] and "UTF-8" in result["error"]
    assert "codec can't decode" not in result["error"]


def test_716_a_readable_file_still_runs(run, home, tmp_path):
    path = tmp_path / "ok.csv"
    path.write_text("a,b\n" + "\n".join(f"{i},x{i % 3}" for i in range(50)), encoding="utf-8")
    code, out, _ = run("demo", "run", "retail", "--input-file", path, "--rows", "200")
    assert code == 0 and "Fidelity" in out

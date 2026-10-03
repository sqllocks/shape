"""HUNT2-scenario: regression tests for defects found in the second audit of ``shape demo``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.demo.connections import ConnectionProfile, check_profile
from shape.demo.errors import DemoError
from test_run_local import session_of  # noqa: E402  (the helper of the sibling module)


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
        ("sql_db_conn_str", "Server=tcp:x.database.windows.net;Database=d;Authentication=ActiveDirectoryDefault"),
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
            out.writerow(
                [i, f"{who.lower()}@secretcorp.example", f"{100 + i % 800}-{10 + i % 80}-{1000 + i}",
                 ["basic", "plus", "pro"][i % 3], ["Springfield", "Gotham"][i % 2]]
            )  # fmt: skip
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

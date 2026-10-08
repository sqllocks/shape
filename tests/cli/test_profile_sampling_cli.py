"""W2-07 items 1 and 4 through the CLI: sampling flags, the note, show, diff and the report."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import shape
from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import orders_table, write_orders_csv, write_shop_dir  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w2_07"
NOTE = "shape: note: profiled a "


def run(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        rc = main(list(args))
    except SystemExit as exc:  # argparse
        rc = int(exc.code or 0)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture()
def orders_csv(tmp_path: Path) -> Path:
    return write_orders_csv(tmp_path / "orders.csv")


def sampling_of(path: Path) -> dict[str, Any]:
    prof = shape.load(str(path))
    return next(iter(prof.tables.values()))["sampling"]  # type: ignore[no-any-return]


# --- flags -------------------------------------------------------------------------------------


def test_sample_rows_is_recorded_and_noted(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "p.shape"
    rc, _, err = run("profile", str(orders_csv), "--sample", "100", "-o", str(out), capsys=capsys)
    assert rc == 0
    assert (
        err.splitlines().count("shape: note: profiled a random sample of 100 of 600 rows (seed 42)")
        == 1
    )
    rec = sampling_of(out)
    assert rec["method"] == "random" and rec["requested"] == {"rows": 100} and rec["seed"] == 42


def test_sample_percentage_method_and_seed(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "p.shape"
    rc, _, err = run(
        "profile", str(orders_csv), "--sample", "25%", "--sample-method", "systematic",
        "--sample-seed", "7", "-o", str(out), capsys=capsys,
    )  # fmt: skip
    assert rc == 0
    assert "shape: note: profiled a systematic sample of 150 of 600 rows (seed 7)" in err
    rec = sampling_of(out)
    assert (
        rec["requested"] == {"fraction": 0.25} and rec["seed"] == 7 and rec["sampled_rows"] == 150
    )


def test_head_takes_the_first_rows(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "p.shape"
    rc, _, err = run(
        "profile", str(orders_csv), "--sample", "40", "--sample-method", "head", "-o", str(out),
        "--capture", "full",  # the extremes of the key are what it checks (W1-11 removes them)
        capsys=capsys,
    )  # fmt: skip
    assert rc == 0 and "profiled a head sample of 40 of 600 rows" in err
    col = next(iter(shape.load(str(out)).tables.values()))["columns"]["order_id"]
    assert col["min_value"][1] == 1 and col["max_value"][1] == 40


def test_the_note_is_never_printed_without_sample(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    rc, _, err = run("profile", str(orders_csv), "-o", str(tmp_path / "p.shape"), capsys=capsys)
    assert rc == 0 and NOTE not in err and "sample" not in err.lower()


def test_a_sample_of_more_rows_than_there_are_still_says_so(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    out = tmp_path / "p.shape"
    rc, _, err = run("profile", str(orders_csv), "--sample", "5000", "-o", str(out), capsys=capsys)
    assert rc == 0
    assert "shape: note: profiled a random sample of 600 of 600 rows (seed 42)" in err
    assert sampling_of(out)["method"] == "none"


def test_the_same_seed_gives_the_same_file_content(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    ids = []
    for name, seed in (("a", "3"), ("b", "3"), ("c", "4")):
        rc, stdout, _ = run(
            "profile", str(orders_csv), "--sample", "90", "--sample-seed", seed, "-o",
            str(tmp_path / f"{name}.shape"), capsys=capsys,
        )  # fmt: skip
        assert rc == 0
        ids.append(json.loads(stdout)["shape_content_id"])
    assert ids[0] == ids[1] and ids[0] != ids[2]


@pytest.mark.parametrize(
    "bad",
    [["--sample", "0"], ["--sample", "-3"], ["--sample", "0%"], ["--sample", "150%"],
     ["--sample", "abc"], ["--sample", "10", "--sample-method", "stratified"],
     ["--sample", "10", "--sample-seed", "-1"], ["--sample", "10", "--sample-seed", "x"],
     ["--sample", "10", "--sample-seed", "4294967296"], ["--sample-method", "head"],
     ["--sample-seed", "5"], ["--sample", ""]],
)  # fmt: skip
def test_invalid_sampling_exits_2_and_writes_nothing(
    orders_csv: Path, tmp_path: Path, capsys: Any, bad: list[str]
) -> None:
    out = tmp_path / "p.shape"
    rc, _, err = run("profile", str(orders_csv), *bad, "-o", str(out), capsys=capsys)
    assert rc == 2 and not out.exists()
    assert err.strip()


def test_a_dataset_samples_each_table_and_keeps_foreign_keys(tmp_path: Path, capsys: Any) -> None:
    folder = write_shop_dir(tmp_path / "shop", customers=200, orders=1000)
    out = tmp_path / "shop.shape"
    rc, _, err = run(
        "profile", str(folder), "--dataset", "--sample", "20%", "-o", str(out), capsys=capsys
    )
    assert rc == 0
    notes = [line for line in err.splitlines() if line.startswith(NOTE)]
    assert len(notes) == 2 and any("of 1,000 rows" in n or "of 1000 rows" in n for n in notes)
    assert all("table" in n for n in notes)
    prof = shape.load(str(out))
    assert prof.tables["customer"]["sampling"]["sampled_rows"] == 40
    assert prof.tables["orders"]["sampling"]["sampled_rows"] == 200
    assert prof.tables["orders"]["detected_fks"] == {"customer_id": "customer"}


def test_a_dataset_with_an_invalid_sample_exits_2(tmp_path: Path, capsys: Any) -> None:
    folder = write_shop_dir(tmp_path / "shop")
    rc, _, _ = run("profile", str(folder), "--dataset", "--sample", "0", "-o",
                   str(tmp_path / "x.shape"), capsys=capsys)  # fmt: skip
    assert rc == 2


# --- show, summary and report ------------------------------------------------------------------


def test_show_prints_the_sampling_record(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    out = tmp_path / "p.shape"
    run("profile", str(orders_csv), "--sample", "100", "-o", str(out), capsys=capsys)
    rc, stdout, _ = run("show", str(out), capsys=capsys)
    doc = json.loads(stdout)
    assert rc == 0
    assert doc["sampling"]["orders"]["method"] == "random"
    assert doc["sampling"]["orders"]["population_rows"] == 600
    assert doc["profile"]["row_count"] == 100


def test_show_of_an_unsampled_profile_says_read_whole(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    out = tmp_path / "p.shape"
    run("profile", str(orders_csv), "-o", str(out), capsys=capsys)
    _, stdout, _ = run("show", str(out), capsys=capsys)
    assert json.loads(stdout)["sampling"]["orders"]["method"] == "none"


def test_show_of_an_old_profile_says_not_recorded(capsys: Any) -> None:
    rc, stdout, _ = run("show", str(FIXTURES / "pre_w2_07_orders.shape"), capsys=capsys)
    assert rc == 0 and json.loads(stdout)["sampling"] == {"orders": "not recorded"}


def test_the_json_summary_carries_the_record(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    out, summary = tmp_path / "p.shape", tmp_path / "s.json"
    run("profile", str(orders_csv), "--sample", "100", "-o", str(out), "--json", str(summary),
        capsys=capsys)  # fmt: skip
    assert json.loads(summary.read_text())["sampling"]["method"] == "random"


def test_the_html_report_shows_the_sampling_record(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    out, html = tmp_path / "p.shape", tmp_path / "r.html"
    run(
        "profile",
        str(orders_csv),
        "--sample",
        "100",
        "-o",
        str(out),
        "--html",
        str(html),
        capsys=capsys,
    )
    text = html.read_text(encoding="utf-8")
    assert "Sampling" in text and "random sample of 100" in text and "600" in text
    assert "limited" in text  # the adequacy
    whole = tmp_path / "w.html"
    run(
        "profile",
        str(orders_csv),
        "-o",
        str(tmp_path / "w.shape"),
        "--html",
        str(whole),
        capsys=capsys,
    )
    assert "read whole" in whole.read_text(encoding="utf-8")


def test_the_html_report_of_an_old_profile_says_not_recorded() -> None:
    html = shape.load(str(FIXTURES / "pre_w2_07_orders.shape")).to_html()
    assert "Sampling" in html and "not recorded" in html


# --- diff --------------------------------------------------------------------------------------


def _profile(csv: Path, path: Path, capsys: Any, *extra: str) -> Path:
    rc, _, _ = run("profile", str(csv), "-o", str(path), *extra, capsys=capsys)
    assert rc == 0
    return path


def test_diff_notes_a_sampled_profile_against_an_unsampled_one(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    a = _profile(orders_csv, tmp_path / "a.shape", capsys, "--sample", "300")
    b = _profile(orders_csv, tmp_path / "b.shape", capsys)
    rc, stdout, err = run("diff", str(a), str(b), "--json", str(tmp_path / "d.json"), capsys=capsys)
    notes = json.loads((tmp_path / "d.json").read_text())["notes"]
    assert rc == 0 and len(notes) == 1 and "sampled" in notes[0] and "orders" in notes[0]
    assert stdout == ""  # --json FILE writes the file instead of standard output (#308)
    assert f"shape: note: {notes[0]}" in err  # and the note is still shown
    _, stdout, _ = run("diff", str(a), str(b), capsys=capsys)
    assert json.loads(stdout)["notes"] == notes


def test_diff_notes_different_methods_and_not_the_same_one(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    a = _profile(orders_csv, tmp_path / "a.shape", capsys, "--sample", "300")
    h = _profile(
        orders_csv, tmp_path / "h.shape", capsys, "--sample", "300", "--sample-method", "head"
    )
    r2 = _profile(
        orders_csv, tmp_path / "r2.shape", capsys, "--sample", "200", "--sample-seed", "9"
    )
    _, stdout, _ = run("diff", str(a), str(h), capsys=capsys)
    notes = json.loads(stdout)["notes"]
    assert len(notes) == 1 and "random" in notes[0] and "head" in notes[0]
    _, stdout, _ = run("diff", str(a), str(r2), capsys=capsys)
    assert "notes" not in json.loads(stdout)  # the same method: nothing to say


def test_diff_has_no_notes_for_two_unsampled_profiles(
    orders_csv: Path, tmp_path: Path, capsys: Any
) -> None:
    a = _profile(orders_csv, tmp_path / "a.shape", capsys)
    b = _profile(orders_csv, tmp_path / "b.shape", capsys)
    _, stdout, _ = run("diff", str(a), str(b), capsys=capsys)
    assert "notes" not in json.loads(stdout)


def test_diff_with_an_old_profile_has_no_notes_and_no_error(capsys: Any, tmp_path: Path) -> None:
    new = tmp_path / "n.shape"
    shape.save(shape.profile(orders_table(200), name="orders"), new)
    rc, stdout, _ = run("diff", str(FIXTURES / "pre_w2_07_orders.shape"), str(new), capsys=capsys)
    assert rc == 0 and json.loads(stdout)["drifted"] is False


def test_row_count_change_compares_the_population_when_both_carry_it(tmp_path: Path) -> None:
    small = shape.profile(orders_table(1000), sample=100)
    big = shape.profile(orders_table(3000), sample=100)
    same = shape.profile(orders_table(1000), sample=100, sample_seed=9)
    changes = shape.diff(small, big).changes
    rc = [c for c in changes if c["kind"] == "row_count_change"]
    assert len(rc) == 1 and rc[0]["baseline"] == 1000 and rc[0]["current"] == 3000
    assert not [c for c in shape.diff(small, same).changes if c["kind"] == "row_count_change"]


def test_row_count_change_uses_rows_when_one_profile_has_no_population() -> None:
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))  # 200 rows, no record
    sampled = shape.profile(orders_table(1000), name="orders", sample=50)
    rc = [c for c in shape.diff(old, sampled).changes if c["kind"] == "row_count_change"]
    assert rc and rc[0]["baseline"] == 200 and rc[0]["current"] == 50


def test_a_note_alone_does_not_fail_the_gate(orders_csv: Path, tmp_path: Path, capsys: Any) -> None:
    a = _profile(orders_csv, tmp_path / "a.shape", capsys, "--sample", "599")
    b = _profile(orders_csv, tmp_path / "b.shape", capsys)
    rc, stdout, _ = run("diff", str(a), str(b), "--fail-on-drift", capsys=capsys)
    doc = json.loads(stdout)
    assert doc["notes"] and rc == int(doc["drifted"])
    assert not [c for c in doc["changes"] if c["kind"] == "row_count_change"]  # same population

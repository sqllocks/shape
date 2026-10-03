"""W5-09 item 4: ``shape fabric check-answers ANSWERS RESULTS`` (alias ``shape check-answers``):
results exported from a DAX client compared with the known answers.

The helper below plays the DAX client: it turns ``answers.json`` into the files a client exports
for ``queries.dax`` (a CSV per query, a JSON per query, one JSON for all of them), in the header
styles the common clients use.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from shape.cli.main import main

pytestmark = pytest.mark.contract

SHOP = json.loads((Path(__file__).parent / "data" / "shop_schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("check")
    schema = base / "shop.json"
    schema.write_text(json.dumps(SHOP), encoding="utf-8")
    out = base / "built"
    measures = {
        "format": "shape-dax-measures",
        "version": 1,
        "measures": [
            {"name": "Items", "table": "item", "aggregation": "count"},
            {"name": "Revenue", "table": "item", "aggregation": "sum", "column": "price"},
            {"name": "Mean Price", "table": "item", "aggregation": "avg", "column": "price"},
            {"name": "Weight", "table": "item", "aggregation": "sum", "column": "weight"},
            {
                "name": "Price per Item",
                "table": "item",
                "aggregation": "ratio",
                "numerator": "Revenue",
                "denominator": "Items",
            },
        ],
        "slice_by": ["category.label", "category.region"],
    }
    mpath = base / "measures.json"
    mpath.write_text(json.dumps(measures), encoding="utf-8")
    code = main(
        ["known-answer", str(schema), "--measures", str(mpath), "--seed", "5", "-o", str(out)]
    )
    assert code == 0
    return out


@pytest.fixture
def run(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def go(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


def answers_doc(built: Path) -> dict:
    return json.loads((built / "answers.json").read_text(encoding="utf-8"))


def text_of(value):
    return "" if value is None else str(value)


def client_rows(q: dict, style: str = "qualified") -> tuple[list[str], list[list[str]]]:
    """The header and rows a DAX client exports for one query of ``answers.json``."""
    group = []
    if q["slice"]:
        table, column = q["slice"].split(".", 1)
        group = [f"{table}[{column}]" if style != "bare" else f"[{column}]"]
    aliases = list(q["measures"])
    head = group + ([f"[{a}]" for a in aliases] if style == "bracket" else aliases)
    rows = []
    for r in q["rows"]:
        rows.append([text_of(k) for k in r["key"]] + [text_of(r["values"].get(a)) for a in aliases])
    return head, rows


def write_folder(doc: dict, folder: Path, style="qualified", kind="csv") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for q in doc["queries"]:
        head, rows = client_rows(q, style)
        if kind == "csv":
            with open(folder / f"{q['id']}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(head)
                w.writerows(rows)
        else:
            (folder / f"{q['id']}.json").write_text(
                json.dumps([dict(zip(head, [(c or None) for c in r], strict=True)) for r in rows]),
                encoding="utf-8",
            )
    return folder


def rewrite_cell(path: Path, match: str, column: int, new: str, row: int = 0) -> None:
    """Change one cell of a CSV: the ``row``-th data row whose first cell contains ``match``."""
    lines = list(csv.reader(open(path, newline="", encoding="utf-8")))
    seen = 0
    for r in lines[1:]:
        if match in r[0] or not match:
            if seen == row:
                r[column] = new
                break
            seen += 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(lines)


# ---- a matching export passes ------------------------------------------------------------------


@pytest.mark.parametrize("style", ["qualified", "bracket", "bare"])
@pytest.mark.parametrize("kind", ["csv", "json"])
def test_an_export_that_matches_exits_0(run, built, tmp_path, style, kind):
    folder = write_folder(answers_doc(built), tmp_path / "res", style, kind)
    code, out, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert (code, err) == (0, ""), out
    assert "match" in out.lower()


def test_the_fabric_group_has_the_command_too(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    code, _, err = run("fabric", "check-answers", str(built / "answers.json"), str(folder))
    assert (code, err) == (0, "")


def test_one_json_file_holding_every_query_by_id(run, built, tmp_path):
    doc = answers_doc(built)
    payload = {}
    for q in doc["queries"]:
        head, rows = client_rows(q)
        payload[q["id"]] = [dict(zip(head, r, strict=True)) for r in rows]
    path = tmp_path / "all.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(path))
    assert (code, err) == (0, "")


def test_one_csv_file_with_a_query_column(run, built, tmp_path):
    doc = answers_doc(built)
    path = tmp_path / "all.csv"
    heads = []
    rows = []
    for q in doc["queries"]:
        head, body = client_rows(q)
        heads.append(head)
        rows.append((q["id"], head, body))
    # one table: every column of every query, the ones a query lacks left blank
    columns = ["query"] + sorted({c for h in heads for c in h})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for qid, head, body in rows:
            for r in body:
                cell = dict(zip(head, r, strict=True))
                w.writerow([qid] + [cell.get(c, "") for c in columns[1:]])
    code, out, err = run("check-answers", str(built / "answers.json"), str(path))
    assert (code, err) == (0, ""), out


def test_a_power_bi_rest_response_is_read(run, built, tmp_path):
    doc = answers_doc(built)
    q = doc["queries"][0]  # the grand total, one query
    only = dict(doc, queries=[q], skipped=[])
    (tmp_path / "one.json").write_text(json.dumps(only), encoding="utf-8")
    head, rows = client_rows(q, "bracket")
    body = {"results": [{"tables": [{"rows": [dict(zip(head, rows[0], strict=True))]}]}]}
    (tmp_path / "pbi.json").write_text(json.dumps(body), encoding="utf-8")
    code, out, err = run("check-answers", str(tmp_path / "one.json"), str(tmp_path / "pbi.json"))
    assert (code, err) == (0, ""), out


def test_numbers_may_be_written_in_any_decimal_form(run, built, tmp_path):
    doc = answers_doc(built)
    folder = write_folder(doc, tmp_path / "res")
    first = folder / "q01.csv"
    rows = list(csv.reader(open(first, newline="", encoding="utf-8")))
    head, body = rows[0], rows[1]
    i = head.index("item.Items")
    body[i] = f"{int(body[i])}.0"  # 300.0 is 300
    j = head.index("item.Revenue")
    body[j] = f"{body[j]}00"  # 1234.50 is 1234.5000
    with open(first, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, out, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert (code, err) == (0, ""), out


# ---- a mismatch exits 1 ------------------------------------------------------------------------


def test_one_changed_digit_in_a_sum_fails(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    q = next(q for q in answers_doc(built)["queries"] if q["slice"] == "category.label")
    head, _ = client_rows(q)
    col = head.index("item.Revenue")
    path = folder / f"{q['id']}.csv"
    first = list(csv.reader(open(path, newline="", encoding="utf-8")))[1]
    old = first[col]
    digit = old[-1]
    new = old[:-1] + str((int(digit) + 1) % 10)
    rewrite_cell(path, "", col, new)
    code, out, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1, out
    assert "item.Revenue" in out and "category.label" in out
    assert f"expected {old}" in out and f"observed {new}" in out
    assert "1 mismatch" in out


def test_one_changed_count_fails(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    path = folder / "q01.csv"
    head = next(csv.reader(open(path, newline="", encoding="utf-8")))
    rewrite_cell(path, "", head.index("item.Items"), "301")
    code, out, _ = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1 and "expected 300" in out and "observed 301" in out


def test_a_sum_is_exact_the_smallest_difference_fails(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    path = folder / "q01.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    j = rows[0].index("item.Revenue")
    from decimal import Decimal

    rows[1][j] = str(Decimal(rows[1][j]) + Decimal("0.0000001"))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, out, _ = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1 and "item.Revenue" in out


def test_a_ratio_is_compared_at_the_answers_places(run, built, tmp_path):
    doc = answers_doc(built)
    q = doc["queries"][0]
    expected = q["rows"][0]["values"]["item.Mean Price"]  # six places
    assert re.fullmatch(r"\d+\.\d{6}", expected)
    folder = write_folder(doc, tmp_path / "res")
    path = folder / "q01.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    j = rows[0].index("item.Mean Price")
    # more digits than the answer: it rounds to the same six places, so it matches
    rows[1][j] = expected + "4"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    assert run("check-answers", str(built / "answers.json"), str(folder))[0] == 0
    # a different sixth place does not
    last = int(expected[-1])
    rows[1][j] = expected[:-1] + str((last + 1) % 10)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, out, _ = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1 and "item.Mean Price" in out


def test_places_compares_the_inexact_values_at_that_many_places(run, built, tmp_path):
    doc = answers_doc(built)
    folder = write_folder(doc, tmp_path / "res")
    path = folder / "q01.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    from decimal import ROUND_HALF_EVEN, Decimal

    j = rows[0].index("item.Mean Price")
    rows[1][j] = str(Decimal(rows[1][j]).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
    k = rows[0].index("item.Price per Item")
    rows[1][k] = str(Decimal(rows[1][k]).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    args = ("check-answers", str(built / "answers.json"), str(folder))
    code, out, _ = run(*args)
    assert code == 1 and "item.Mean Price" in out  # at six places a two-place export differs
    code, out, err = run(*args, "--places", "2")
    assert (code, err) == (0, ""), out
    # counts and sums stay exact whatever --places says
    rewrite_cell(path, "", rows[0].index("item.Items"), "299")
    code, out, _ = run(*args, "--places", "2")
    assert code == 1 and "item.Items" in out


def test_a_bad_places_value_exits_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    for bad in ("-1", "x", "40"):
        code, _, err = run(
            "check-answers", str(built / "answers.json"), str(folder), "--places", bad
        )
        assert code == 2, bad


def test_a_missing_group_and_an_extra_group_are_mismatches(run, built, tmp_path):
    doc = answers_doc(built)
    folder = write_folder(doc, tmp_path / "res")
    q = next(q for q in doc["queries"] if q["slice"] == "category.label")
    path = folder / f"{q['id']}.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    removed = rows.pop(1)
    extra = ["zzz"] + rows[1][1:]
    rows.append(extra)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, out, _ = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1
    assert removed[0] in out and "observed (none)" in out
    assert "zzz" in out and "expected (none)" in out


def test_a_blank_cell_where_a_value_is_expected_is_a_mismatch(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    path = folder / "q01.csv"
    head = next(csv.reader(open(path, newline="", encoding="utf-8")))
    rewrite_cell(path, "", head.index("item.Revenue"), "")
    code, out, _ = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 1 and "item.Revenue" in out and "observed (blank)" in out


def test_at_most_fifty_mismatches_are_printed_and_the_rest_counted(run, built, tmp_path):
    doc = answers_doc(built)
    # a copy of the answers with 70 groups, every value wrong in the export
    q = {
        "id": "q01",
        "slice": "category.label",
        "slice_type": "string",
        "measures": ["item.Items"],
        "rows": [{"key": [f"g{i:03d}"], "values": {"item.Items": str(i + 1)}} for i in range(70)],
    }
    big = dict(doc, queries=[q], skipped=[])
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(big), encoding="utf-8")
    folder = tmp_path / "res"
    folder.mkdir()
    with open(folder / "q01.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category[label]", "item.Items"])
        for i in range(70):
            w.writerow([f"g{i:03d}", str(i + 1000)])
    code, out, _ = run("check-answers", str(answers), str(folder))
    assert code == 1
    assert out.count("expected ") == 50
    assert "70 mismatches" in out and "20 more" in out


# ---- exit 2: the files are wrong ---------------------------------------------------------------


def test_a_missing_query_result_exits_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    (folder / "q02.csv").unlink()
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "q02" in err


def test_a_result_for_an_unknown_query_exits_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    (folder / "q99.csv").write_text("a\n1\n", encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "q99" in err


def test_files_of_other_kinds_in_the_folder_are_not_results(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    (folder / "README.txt").write_text("hello", encoding="utf-8")
    (folder / ".hidden").write_text("x", encoding="utf-8")
    assert run("check-answers", str(built / "answers.json"), str(folder))[0] == 0


def test_a_result_missing_a_column_exits_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    path = folder / "q01.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    i = rows[0].index("item.Revenue")
    for r in rows:
        del r[i]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "item.Revenue" in err


@pytest.mark.parametrize(
    ("cell", "needle"),
    [("abc", "abc"), ("NaN", "NaN"), ("1,234", "1,234"), ("Infinity", "Infinity")],
)
def test_a_cell_that_is_not_a_number_exits_2(run, built, tmp_path, cell, needle):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    path = folder / "q01.csv"
    head = next(csv.reader(open(path, newline="", encoding="utf-8")))
    rewrite_cell(path, "", head.index("item.Items"), cell)
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2, err
    assert needle in err


def test_an_empty_csv_and_a_repeated_column_exit_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    (folder / "q01.csv").write_text("", encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "empty" in err
    (folder / "q01.csv").write_text("item.Items,item.Items\n1,1\n", encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "twice" in err


def test_a_malformed_json_result_exits_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res", kind="json")
    (folder / "q01.json").write_text("{nope", encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "q01.json" in err


def test_a_duplicate_group_exits_2(run, built, tmp_path):
    doc = answers_doc(built)
    folder = write_folder(doc, tmp_path / "res")
    q = next(q for q in doc["queries"] if q["slice"] == "category.label")
    path = folder / f"{q['id']}.csv"
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    rows.append(rows[1])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    code, _, err = run("check-answers", str(built / "answers.json"), str(folder))
    assert code == 2 and "twice" in err


def test_a_single_file_needs_a_query_column_when_there_are_several_queries(run, built, tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("item.Items\n300\n", encoding="utf-8")
    code, _, err = run("check-answers", str(built / "answers.json"), str(path))
    assert code == 2 and "query" in err


def test_answers_that_are_not_answers_exit_2(run, built, tmp_path):
    folder = write_folder(answers_doc(built), tmp_path / "res")
    for name, content, needle in (
        ("a.json", "{nope", "a.json"),
        ("b.json", "[]", "shape-dax-answers"),
        ("c.json", json.dumps({"format": "x", "version": 1}), "shape-dax-answers"),
        ("d.json", json.dumps({"format": "shape-dax-answers", "version": 2}), "version 2"),
        ("e.json", json.dumps({"format": "shape-dax-answers", "version": "1"}), "version"),
    ):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        code, _, err = run("check-answers", str(p), str(folder))
        assert code == 2 and needle in err, (name, err)
    code, _, err = run("check-answers", str(tmp_path / "missing.json"), str(folder))
    assert code == 2
    code, _, err = run("check-answers", str(built / "answers.json"), str(tmp_path / "nowhere"))
    assert code == 2


def test_the_planted_answer_is_what_the_check_compares(run, tmp_path):
    schema = tmp_path / "shop.json"
    schema.write_text(json.dumps(SHOP), encoding="utf-8")
    out = tmp_path / "o"
    assert (
        main(
            [
                "known-answer",
                str(schema),
                "--seed",
                "5",
                "--plant",
                "item.price=2000",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    doc = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    folder = write_folder(doc, tmp_path / "res")
    assert run("check-answers", str(out / "answers.json"), str(folder))[0] == 0
    # an export of the unplanted data fails against the planted answers
    plain = tmp_path / "plain"
    assert main(["known-answer", str(schema), "--seed", "5", "-o", str(plain)]) == 0
    other = write_folder(
        json.loads((plain / "answers.json").read_text(encoding="utf-8")), tmp_path / "res2"
    )
    code, text, _ = run("check-answers", str(out / "answers.json"), str(other))
    assert code == 1 and "item.Total Price" in text

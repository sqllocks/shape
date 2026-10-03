"""W5-09: compatibility of the persisted formats ``shape-dax-answers`` and ``shape-dax-measures``
(``shape-drift-report`` has its own in ``test_publish_report.py``).

``data/answers_v1.json``, ``data/measures_v1.json`` and ``data/results_v1/`` were written by
version 1 and are frozen: every later version of Shape must still read them. A format that changes
shape gets a new version number, and the corpus of the old one stays.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shape_fabric import known_answer

from shape.cli.main import main

pytestmark = pytest.mark.contract

DATA = Path(__file__).parent / "data"
ANSWERS = DATA / "answers_v1.json"
MEASURES = DATA / "measures_v1.json"
RESULTS = DATA / "results_v1"
SCHEMA = DATA / "shop_schema.json"


@pytest.fixture
def run(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def go(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


def test_the_formats_declare_a_name_and_an_integer_version():
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    measures = json.loads(MEASURES.read_text(encoding="utf-8"))
    assert (answers["format"], answers["version"]) == ("shape-dax-answers", 1)
    assert (measures["format"], measures["version"]) == ("shape-dax-measures", 1)
    assert isinstance(answers["version"], int) and isinstance(measures["version"], int)
    assert known_answer.ANSWERS_VERSION == 1 and known_answer.MEASURES_VERSION == 1


def test_the_v1_answers_still_read_and_check_a_v1_export(run):
    doc = known_answer.load_answers(ANSWERS)
    assert [q["id"] for q in doc["queries"]] == ["q01", "q02", "q03"]
    code, out, err = run("check-answers", str(ANSWERS), str(RESULTS))
    assert (code, err) == (0, ""), out


def test_the_v1_export_with_one_changed_digit_still_fails(run, tmp_path):
    folder = tmp_path / "res"
    folder.mkdir()
    for f in RESULTS.iterdir():
        folder.joinpath(f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    first = folder / "q01.csv"
    lines = first.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split(",")
    cells[1] = cells[1][:-1] + str((int(cells[1][-1]) + 1) % 10)
    lines[1] = ",".join(cells)
    first.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, out, _ = run("check-answers", str(ANSWERS), str(folder))
    assert code == 1 and "1 mismatch" in out


def test_the_v1_measures_still_build_the_v1_answers(run, tmp_path):
    out = tmp_path / "o"
    code, _, err = run(
        "known-answer", str(SCHEMA), "--measures", str(MEASURES), "--seed", "5", "-o", str(out)
    )
    assert (code, err) == (0, "")
    now = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    then = json.loads(ANSWERS.read_text(encoding="utf-8"))
    assert [m["id"] for m in now["measures"]] == [m["id"] for m in then["measures"]]
    assert [q["slice"] for q in now["queries"]] == [q["slice"] for q in then["queries"]]


def test_this_version_writes_the_layout_of_the_v1_corpus(run, tmp_path):
    out = tmp_path / "o"
    code, _, _ = run(
        "known-answer",
        str(SCHEMA),
        "--measures",
        str(MEASURES),
        "--seed",
        "5",
        "--plant",
        "item.price=1500",
        "-o",
        str(out),
    )
    assert code == 0
    now = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    then = json.loads(ANSWERS.read_text(encoding="utf-8"))
    assert list(now) == list(then)
    assert list(now["measures"][0]) == list(then["measures"][0])
    assert list(now["measures"][5]) == list(then["measures"][5])  # the ratio's operands
    assert list(now["queries"][1]) == list(then["queries"][1])
    assert list(now["queries"][1]["rows"][0]) == list(then["queries"][1]["rows"][0])
    assert list(now["queries"][0]["rows"][0]) == list(then["queries"][0]["rows"][0])
    assert list(now["plants"][0]) == list(then["plants"][0])


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update(version=2), "version 2"),
        (lambda d: d.update(version=99), "version 99"),
        (lambda d: d.update(version="1"), "version"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(version=True), "version"),
        (lambda d: d.update(version=None), "version"),
        (lambda d: d.update(format="shape-dax-measures"), "shape-dax-answers"),
        (lambda d: d.pop("format"), "shape-dax-answers"),
    ],
)
def test_a_newer_or_foreign_answers_file_is_refused(run, tmp_path, mutate, needle):
    doc = json.loads(ANSWERS.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, _, err = run("check-answers", str(path), str(RESULTS))
    assert code == 2 and needle in err, err


def test_the_refusal_names_both_versions(tmp_path):
    doc = json.loads(ANSWERS.read_text(encoding="utf-8"))
    doc["version"] = 7
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(known_answer.KnownAnswerError, match=r"version 7.*version 1.*upgrade"):
        known_answer.load_answers(path)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update(version=2), "version 2"),
        (lambda d: d.update(version="1"), "version"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(format="shape-dax-answers"), "shape-dax-measures"),
        (lambda d: d.pop("format"), "shape-dax-measures"),
    ],
)
def test_a_newer_or_foreign_measures_file_is_refused(run, tmp_path, mutate, needle):
    doc = json.loads(MEASURES.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, _, err = run("known-answer", str(SCHEMA), "--measures", str(path), "-o", "o")
    assert code == 2 and needle in err, err
    assert not (tmp_path / "o").exists()

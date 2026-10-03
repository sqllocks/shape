"""ISS-cli #28 for ``shape profile registry``: it can hold the safe form, and says when it holds
real values."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shape.cli.main import main

EMAIL = re.compile(r"[a-z0-9.]+@[a-z0-9.]+\.[a-z]+")


def _all_text(root: Path) -> str:
    import zipfile

    out = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if zipfile.is_zipfile(f):
            with zipfile.ZipFile(f) as z:
                out += [z.read(n).decode("utf-8", "ignore") for n in z.namelist()]
        else:
            out.append(f.read_text("utf-8", "ignore"))
    return "\n".join(out)


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    monkeypatch.delenv("SHAPE_PROFILE_REGISTRY", raising=False)
    for tag, offset in (("a", 0), ("b", 7)):
        rows = "\n".join(f"{i},user{i}@example.com,{20 + (i + offset) % 40}" for i in range(500))
        (tmp_path / f"customers_{tag}.csv").write_text("id,email,age\n" + rows + "\n")
    return tmp_path


def _save(*extra: str, src: str = "customers_a.csv", name: str = "q1") -> int:
    return main(
        ["profile", "registry", "save", src, "--system", "crm", "--name", name, "--root", "preg"]
        + list(extra)
    )


def test_safe_save_stores_no_email(work: Path) -> None:
    assert _save("--safe") == 0
    assert not EMAIL.findall(_all_text(work / "preg"))
    stored = work / "preg" / "crm" / "customers_a" / "q1.safe.json"
    assert stored.is_file()
    assert main(["profile", "validate", "--safe", str(stored)]) == 0


def test_safe_save_from_a_profile_artifact(work: Path) -> None:
    assert main(["profile", "customers_a.csv", "-o", "c.shape"]) == 0
    assert _save("--safe", src="c.shape") == 0
    assert not EMAIL.findall(_all_text(work / "preg"))


def test_raw_save_says_it_holds_real_values(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--capture", "full") == 0
    err = capsys.readouterr().err
    assert "real values" in err and "--capture full" in err
    assert EMAIL.findall(_all_text(work / "preg"))  # the raw form is still what it was


def test_safe_save_does_not_warn(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe") == 0
    assert "real values" not in capsys.readouterr().err


def test_list_shows_the_form(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe") == 0
    assert _save(name="q2") == 0
    capsys.readouterr()
    assert main(["profile", "registry", "list", "--root", "preg", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["q1"]["form"] == "safe" and rows["q1"]["source_rows"] == 500
    assert "form" not in rows["q2"] or rows["q2"]["form"] == "raw"
    assert main(["profile", "registry", "list", "--root", "preg"]) == 0
    assert "safe" in capsys.readouterr().out


def test_tags_survive_a_reindex(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe", "--tags", "prod,daily", "--description", "customers") == 0
    assert (
        main(["profile", "registry", "tag", "crm/customers_a/q1", "reviewed", "--root", "preg"])
        == 0
    )
    assert main(["profile", "registry", "reindex", "--root", "preg"]) == 0
    capsys.readouterr()
    assert main(["profile", "registry", "list", "--root", "preg", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["tags"] == ["daily", "prod", "reviewed"] and row["description"] == "customers"
    assert row["form"] == "safe"
    assert main(["profile", "registry", "validate", "--root", "preg"]) == 0


def test_diff_two_safe_profiles(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe", name="q1") == 0
    assert _save("--safe", src="customers_b.csv", name="q2") == 0
    capsys.readouterr()
    code = main(
        ["profile", "registry", "diff", "crm/customers_a/q1", "crm/customers_b/q2", "--root",
         "preg", "--json"]
    )  # fmt: skip
    assert code == 0
    d = json.loads(capsys.readouterr().out)
    assert "age" in d["changed"] or d["changed"] == {}


def test_validate_data_against_a_safe_profile(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _save("--safe") == 0
    capsys.readouterr()
    code = main(
        ["profile", "registry", "validate", "crm/customers_a/q1", "--data", "customers_a.csv",
         "--root", "preg"]
    )  # fmt: skip
    assert code == 0, capsys.readouterr().err


def test_validate_flags_a_leak_in_a_safe_entry(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _save("--safe") == 0
    path = work / "preg" / "crm" / "customers_a" / "q1.safe.json"
    doc = json.loads(path.read_text())
    doc["tables"]["customers_a"]["columns"]["email"]["note"] = "jane.doe@example.com"
    path.write_text(json.dumps(doc))
    capsys.readouterr()
    assert main(["profile", "registry", "validate", "crm/customers_a/q1", "--root", "preg"]) == 1


def test_one_form_per_identity(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe") == 0
    assert _save() == 2  # the identity is taken, in the other form
    assert "--overwrite" in capsys.readouterr().err
    assert _save("--overwrite") == 0
    assert not (work / "preg" / "crm" / "customers_a" / "q1.safe.json").exists()
    assert (work / "preg" / "crm" / "customers_a" / "q1.shape").is_file()


def test_delete_removes_a_safe_entry(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--safe") == 0
    assert main(["profile", "registry", "delete", "crm/customers_a/q1", "--root", "preg"]) == 0
    assert not list((work / "preg").rglob("*.safe.json"))
    capsys.readouterr()
    assert main(["profile", "registry", "list", "--root", "preg", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_safe_options_need_safe(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _save("--sensitive") == 2
    assert "--safe" in capsys.readouterr().err


def test_a_description_that_looks_like_personal_data_is_refused(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _save("--safe", "--description", "owner jane.doe@example.com") == 2
    assert "leak" in capsys.readouterr().err
    assert not list((work / "preg").rglob("*.safe.json"))

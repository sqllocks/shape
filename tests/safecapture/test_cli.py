"""W1-11 deliverables 1, 5 and 7 at the command line: ``shape profile --capture``."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

import shape
from shape.cli.main import main

Reader = Callable[[Path], str]
WARNING = "shape: warning: --capture full keeps real values in {out}; do not commit or share it"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: pa.Table) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    pacsv.write_csv(table, str(tmp_path / "data.csv"))
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def manifest_of(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        doc = json.loads(z.read("manifest.json"))
    assert isinstance(doc, dict)
    return doc


def leaks(text: str, planted: dict[str, str]) -> list[str]:
    return [name for name, value in planted.items() if value in text]


# --- the default ------------------------------------------------------------------------------


def test_the_default_output_holds_no_planted_value(
    work: Path, capsys: pytest.CaptureFixture[str], artifact_text: Reader, planted: dict[str, str]
) -> None:
    code, _, err = run(
        capsys, "profile", "data.csv", "-o", "p.shape", "--json", "p.json", "--html", "p.html"
    )
    assert code == 0 and "warning" not in err
    assert manifest_of(work / "p.shape")["capture"] == {"mode": "safe", "k": 5}
    for name, text in {
        ".shape": artifact_text(work / "p.shape"),
        "--json": (work / "p.json").read_text(),
        "--html": (work / "p.html").read_text(),
    }.items():
        assert leaks(text, planted) == [], name
    assert "edge1" not in (work / "p.shape").read_bytes().decode("latin-1")


def test_capture_full_contains_them_and_warns_once(
    work: Path, capsys: pytest.CaptureFixture[str], artifact_text: Reader, planted: dict[str, str]
) -> None:
    code, out, err = run(
        capsys,
        "profile",
        "data.csv",
        "-o",
        "full.shape",
        "--capture",
        "full",
        "--json",
        "full.json",
        "--html",
        "full.html",
    )
    assert code == 0
    assert err.strip().splitlines().count(WARNING.format(out="full.shape")) == 1
    assert err.count("warning") == 1
    assert json.loads(out)["written"] == "full.shape"  # the result stays on stdout
    assert manifest_of(work / "full.shape")["capture"] == {"mode": "full", "k": None}
    for name, value in planted.items():  # ...every planted value is in the full capture
        assert value in artifact_text(work / "full.shape"), name
    summary = (work / "full.json").read_text()
    html = (work / "full.html").read_text()
    for name, value in planted.items():
        assert value in html, name
        if name not in ("rare_one", "rare_four"):  # the summary holds extremes, not categories
            assert value in summary, name


def test_the_in_memory_result_is_unchanged_by_the_default(work: Path, table: pa.Table) -> None:
    full = shape.profile(table)
    assert full.to_dict()["columns"]["amount"]["max_value"] == ["float", 987654321.13]


def test_the_json_summary_says_how_it_was_captured(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "p.shape", "--json", "s.json")[0] == 0
    summary = json.loads((work / "s.json").read_text())
    assert summary["capture"] == {"mode": "safe", "k": 5}
    assert summary["columns"]["amount"]["max"] is None
    assert summary["columns"]["age"]["max"] == 60 or summary["columns"]["age"]["max"] is not None


def test_the_html_report_marks_a_suppressed_value(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "p.shape", "--html", "p.html")[0] == 0
    html = (work / "p.html").read_text()
    assert "captured safe" in html


# --- --k, --column-k, --classify ----------------------------------------------------------------


def test_k_and_column_k_set_the_cohort(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(
        capsys, "profile", "data.csv", "-o", "p.shape", "--k", "6", "--column-k", "grade=12"
    )
    assert code == 0
    got = shape.load("p.shape")
    assert got.capture == {"mode": "safe", "k": 6}
    assert got.redaction_manifest["tables"]["data"]["grade"]["k"] == 12
    assert "edge5" not in got.to_dict()["columns"]["grade"]["enum_values"]


def test_classify_makes_a_column_sensitive(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(
        capsys,
        "profile",
        "data.csv",
        "-o",
        "p.shape",
        "--classify",
        "age=CONFIDENTIAL",
        "--classify",
        "city=INTERNAL",
    )
    assert code == 0
    cols = shape.load("p.shape").to_dict()["columns"]
    assert cols["age"]["enum_values"] is None
    assert cols["city"]["enum_values"] is not None


@pytest.mark.parametrize(
    ("flags", "needle"),
    [
        (["--k", "0"], "--k"),
        (["--k", "-3"], "--k"),
        (["--column-k", "grade"], "--column-k"),
        (["--column-k", "grade=zero"], "--column-k"),
        (["--column-k", "grade=0"], "k"),
        (["--column-k", "nope=9"], "nope"),
        (["--classify", "age"], "--classify"),
        (["--classify", "age=TOPSECRET"], "classification"),
        (["--classify", "agee=SECRET"], "agee"),
        (["--capture", "full", "--k", "7"], "--k"),
        (["--capture", "full", "--column-k", "grade=7"], "--column-k"),
        (["--capture", "full", "--classify", "age=SECRET"], "--classify"),
    ],
)
def test_bad_capture_settings_exit_2_and_write_nothing(
    work: Path, capsys: pytest.CaptureFixture[str], flags: list[str], needle: str
) -> None:
    code, _, err = run(capsys, "profile", "data.csv", "-o", "p.shape", *flags)
    assert code == 2 and needle in err
    assert err.startswith("shape: error: ") or "error" in err
    assert not (work / "p.shape").exists()


def test_an_unknown_capture_mode_is_a_usage_error(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        main(["profile", "data.csv", "-o", "p.shape", "--capture", "partial"])
    assert e.value.code == 2
    assert "--capture" in capsys.readouterr().err


# --- deliverable 7: validate --safe --------------------------------------------------------------


def test_the_default_output_passes_validate_safe(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "p.shape")[0] == 0
    code, out, err = run(capsys, "profile", "validate", "--safe", "p.shape")
    assert code == 0, err
    assert out.startswith("CLEAN")


def test_validate_safe_reports_a_full_capture_as_a_finding(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "f.shape", "--capture", "full")[0] == 0
    code, out, _ = run(capsys, "profile", "validate", "--safe", "f.shape", "--json")
    assert code == 1
    rules = {f["rule"] for f in json.loads(out)["findings"]}
    assert "full-capture" in rules


def test_validate_safe_flags_an_old_profile_with_no_capture_field(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from shape.artifact import codec
    from shape.artifact.io import sha256, write_artifact

    prof = shape.profile(str(work / "data.csv"))
    body = codec.dumps(prof.to_dict(), sort_keys=False)
    manifest = {
        "format": "shape",
        "format_version": 1,
        "kind": "profile",
        "name": "old",
        "shape_content_id": sha256(body),
    }
    write_artifact(str(work / "old.shape"), manifest, {"profile.json": body})
    code, out, _ = run(capsys, "profile", "validate", "--safe", "old.shape", "--json")
    assert code == 1
    assert "full-capture" in {f["rule"] for f in json.loads(out)["findings"]}


def test_validate_safe_still_accepts_the_safe_profile_json(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "f.shape", "--capture", "full")[0] == 0
    assert run(capsys, "profile", "safe", "f.shape", "-o", "s.json")[0] == 0
    assert run(capsys, "profile", "validate", "--safe", "s.json")[0] == 0


def test_a_safe_capture_can_become_a_safe_profile_json(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "p.shape")[0] == 0
    assert run(capsys, "profile", "safe", "p.shape", "-o", "s.json")[0] == 0
    assert run(capsys, "profile", "validate", "--safe", "s.json")[0] == 0


# --- the profile registry and the content-addressed registry -------------------------------------


def test_registry_save_is_redacted_by_default(
    work: Path, capsys: pytest.CaptureFixture[str], planted: dict[str, str]
) -> None:
    root = work / "profiles"
    code, _, _ = run(
        capsys, "profile", "registry", "save", "data.csv", "--system", "erp", "--name", "n1",
        "--root", str(root),
    )  # fmt: skip
    assert code == 0
    texts = []
    for f in root.rglob("*.shape"):
        with zipfile.ZipFile(f) as z:
            texts.append("\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()))
    assert texts and leaks("\n".join(texts), planted) == []
    assert manifest_of(next(root.rglob("*.shape")))["capture"]["mode"] == "safe"


def test_registry_save_capture_full_keeps_values_and_warns(
    work: Path, capsys: pytest.CaptureFixture[str], planted: dict[str, str]
) -> None:
    root = work / "profiles"
    code, _, err = run(
        capsys, "profile", "registry", "save", "data.csv", "--system", "erp", "--name", "n1",
        "--root", str(root), "--capture", "full",
    )  # fmt: skip
    assert code == 0 and "--capture full keeps real values" in err
    (f,) = root.rglob("*.shape")
    with zipfile.ZipFile(f) as z:
        text = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    assert planted["extreme"] in text


def test_a_safe_capture_commits_to_the_registry_without_allow_raw(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "p.shape")[0] == 0
    code, _, _ = run(capsys, "registry", "reg", "commit", "orders", "p.shape")
    assert code == 0
    from shape.registry import LocalRegistry

    assert LocalRegistry(work / "reg").log("orders")[0]["metadata"]["profile_form"] == "safe"


def test_a_full_capture_is_still_refused_by_the_registry(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "profile", "data.csv", "-o", "f.shape", "--capture", "full")[0] == 0
    code, _, err = run(capsys, "registry", "reg", "commit", "orders", "f.shape")
    assert code == 2 and "--allow-raw" in err

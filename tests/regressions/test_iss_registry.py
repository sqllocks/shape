"""ISS-cli #28 and #29: ``shape registry`` stores no raw profile unless asked, and its CLI is
usable (metadata, named arguments, readable log, list, show, diff, checkout to a file)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from shape.cli.main import main
from shape.registry import LocalRegistry, RegistryError

EMAIL = re.compile(r"[a-z0-9.]+@[a-z0-9.]+\.[a-z]+")


def _stored_text(reg: Path) -> str:
    """Everything the registry holds, decoded: zip members and plain objects alike."""
    import zipfile

    out = []
    for f in (reg / "objects").iterdir():
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
    rows = "\n".join(f"{i},user{i}@example.com,{20 + i % 40}" for i in range(500))
    (tmp_path / "customers.csv").write_text("id,email,age\n" + rows + "\n")
    assert main(["profile", "customers.csv", "-o", "cust.shape"]) == 0
    return tmp_path


def _json_out(capsys: pytest.CaptureFixture[str]) -> object:
    return json.loads(capsys.readouterr().out)


# -- #28: no raw values in a registry unless asked --------------------------------------------


def test_raw_profile_is_refused_and_nothing_is_stored(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["registry", "reg", "commit", "customers", "cust.shape"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("shape: error: ") and "--safe" in err and "--allow-raw" in err
    assert list((work / "reg" / "objects").iterdir()) == []
    assert not (work / "reg" / "logs" / "customers.jsonl").exists()


def test_safe_commit_stores_no_email(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "reg", "commit", "customers", "cust.shape", "--safe"]) == 0
    cid = _json_out(capsys)["content_id"]  # type: ignore[index]
    assert not EMAIL.findall(_stored_text(work / "reg"))
    (obj,) = (work / "reg" / "objects").iterdir()
    assert obj.name == cid == hashlib.sha256(obj.read_bytes()).hexdigest()
    assert main(["profile", "validate", "--safe", str(obj)]) == 0
    entry = LocalRegistry(work / "reg").log("customers")[0]
    assert entry["metadata"]["profile_form"] == "safe"


def test_safe_commit_is_reproducible(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "reg", "commit", "customers", "cust.shape", "--safe"]) == 0
    first = _json_out(capsys)["content_id"]  # type: ignore[index]
    assert main(["registry", "reg", "commit", "customers", "cust.shape", "--safe"]) == 0
    assert _json_out(capsys)["content_id"] == first  # type: ignore[index]


def test_profile_safe_output_commits_as_is(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "safe", "cust.shape", "-o", "cust.safe.json"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "customers", "cust.safe.json"]) == 0
    cid = _json_out(capsys)["content_id"]  # type: ignore[index]
    assert cid == hashlib.sha256((work / "cust.safe.json").read_bytes()).hexdigest()
    assert not EMAIL.findall(_stored_text(work / "reg"))
    assert LocalRegistry(work / "reg").log("customers")[0]["metadata"]["profile_form"] == "safe"


def test_unsafe_safe_profile_is_refused(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "safe", "cust.shape", "-o", "full.json", "--unsafe-full-fidelity"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "customers", "full.json"]) == 1
    assert "leak" in capsys.readouterr().err.lower()
    assert list((work / "reg" / "objects").iterdir()) == []


def test_exported_profile_json_is_raw_too(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "export", "cust.shape", "-o", "cust.json"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "customers", "cust.json"]) == 2
    assert "--safe" in capsys.readouterr().err


def test_allow_raw_stores_it_and_warns(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "reg", "commit", "customers", "cust.shape", "--allow-raw"]) == 0
    captured = capsys.readouterr()
    assert "real values" in captured.err
    assert EMAIL.findall(_stored_text(work / "reg"))  # the choice is honoured
    assert LocalRegistry(work / "reg").log("customers")[0]["metadata"]["profile_form"] == "raw"


def test_safe_needs_a_profile(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (work / "note.txt").write_text("hello")
    assert main(["registry", "reg", "commit", "note", "note.txt", "--safe"]) == 2
    assert "profile" in capsys.readouterr().err


def test_other_content_is_stored_as_before(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (work / "note.txt").write_text("hello")
    assert main(["registry", "reg", "commit", "note", "note.txt"]) == 0
    cid = _json_out(capsys)["content_id"]  # type: ignore[index]
    assert cid == hashlib.sha256(b"hello").hexdigest()
    assert LocalRegistry(work / "reg").checkout("note") == b"hello"


def test_library_commit_refuses_a_raw_profile(work: Path) -> None:
    raw = (work / "cust.shape").read_bytes()
    reg = LocalRegistry(work / "lib")
    with pytest.raises(RegistryError, match="raw profile"):
        reg.commit("customers", raw)
    assert reg.commit("customers", raw, allow_raw=True) == hashlib.sha256(raw).hexdigest()


# -- #29: a usable CLI -----------------------------------------------------------------------


def _commit(*extra: str, name: str = "orders") -> list[str]:
    return ["registry", "reg", "commit", name, "orders.txt", *extra]


@pytest.fixture
def orders(work: Path) -> Path:
    (work / "orders.txt").write_text("v1")
    return work


def test_metadata_is_recorded(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _commit("--meta", "run_id=r-17", "--meta", "source=crm", "--business-date", "2026-06-02")
    assert main(args) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "log", "orders"]) == 0
    (entry,) = _json_out(capsys)  # type: ignore[misc]
    assert entry["metadata"] == {
        "run_id": "r-17",
        "source": "crm",
        "business_date": "2026-06-02",
    }


@pytest.mark.parametrize(
    "extra",
    [["--meta", "novalue"], ["--meta", "=x"], ["--meta", "a=1", "--meta", "a=2"]],
    ids=["no-equals", "no-key", "duplicate"],
)
def test_bad_metadata_is_an_input_error(
    orders: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_commit(*extra)) == 2
    assert capsys.readouterr().err.startswith("shape: error: ")
    assert not (orders / "reg" / "logs" / "orders.jsonl").exists()


def test_bad_business_date_is_an_input_error(
    orders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_commit("--business-date", "yesterday")) == 2
    assert "business date" in capsys.readouterr().err


def test_log_has_a_readable_time(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_commit()) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "log", "orders"]) == 0
    (entry,) = _json_out(capsys)  # type: ignore[misc]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry["created"])
    assert isinstance(entry["created_at"], float)


def test_log_of_an_unknown_name_is_an_error(
    orders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["registry", "reg", "log", "nothing"]) == 2
    assert "nothing" in capsys.readouterr().err


def test_list_names(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "reg", "list"]) == 0
    assert _json_out(capsys) == []
    assert main(_commit()) == 0
    (orders / "orders.txt").write_text("v2")
    assert main(_commit()) == 0
    assert main(_commit(name="other")) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "tag", "orders", "v2"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "list"]) == 0
    rows = _json_out(capsys)
    assert [r["name"] for r in rows] == ["orders", "other"]  # type: ignore[union-attr]
    assert rows[0]["commits"] == 2 and rows[0]["tags"] == ["v2"]  # type: ignore[index]
    assert rows[0]["latest"] == hashlib.sha256(b"v2").hexdigest()  # type: ignore[index]


def test_show_one_entry(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_commit("--meta", "k=v")) == 0
    cid = _json_out(capsys)["content_id"]  # type: ignore[index]
    assert main(["registry", "reg", "show", "orders"]) == 0
    entry = _json_out(capsys)
    assert entry["content_id"] == cid and entry["metadata"] == {"k": "v"}  # type: ignore[index]
    assert main(["registry", "reg", "show", "orders", "nope"]) == 2
    assert "orders@nope" in capsys.readouterr().err


def test_diff_two_refs(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (orders / "a.json").write_text('{"rows": 10, "cols": ["a"], "keep": 1}')
    (orders / "b.json").write_text('{"rows": 12, "cols": ["a", "b"], "keep": 1}')
    assert main(["registry", "reg", "commit", "t", "a.json"]) == 0
    assert main(["registry", "reg", "commit", "t", "b.json"]) == 0
    assert main(["registry", "reg", "tag", "t", "old", "latest"]) == 0
    capsys.readouterr()
    reg = LocalRegistry(orders / "reg")
    first = reg.log("t")[0]["content_id"]
    assert main(["registry", "reg", "diff", "t", first, "latest"]) == 0
    d = _json_out(capsys)
    assert d["same"] is False  # type: ignore[index]
    assert set(d["changed"]) == {"rows", "cols"}  # type: ignore[index]
    assert main(["registry", "reg", "diff", "t", "latest", "old"]) == 0
    assert _json_out(capsys)["same"] is True  # type: ignore[index]


def test_diff_unknown_ref_is_an_error(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_commit()) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "diff", "orders", "latest", "nope"]) == 2
    assert "orders@nope" in capsys.readouterr().err


def test_checkout_to_a_file(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_commit()) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "checkout", "orders", "-o", "out.bin"]) == 0
    assert (orders / "out.bin").read_bytes() == b"v1"
    assert _json_out(capsys) == {"written": "out.bin"}


def test_checkout_legacy_output_position_still_works(
    orders: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_commit()) == 0
    assert main(["registry", "reg", "checkout", "orders", "latest", "legacy.bin"]) == 0
    assert (orders / "legacy.bin").read_bytes() == b"v1"


def test_checkout_refuses_to_write_binary_to_a_terminal(
    orders: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from shape.cli import registry as cli_registry

    assert main(_commit()) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli_registry, "_stdout_is_terminal", lambda: True)
    assert main(["registry", "reg", "checkout", "orders"]) == 2
    assert "-o" in capsys.readouterr().err


def test_checkout_to_a_pipe_still_streams(
    orders: Path, monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    from shape.cli import registry as cli_registry

    assert main(_commit()) == 0
    capfdbinary.readouterr()
    monkeypatch.setattr(cli_registry, "_stdout_is_terminal", lambda: False)
    assert main(["registry", "reg", "checkout", "orders"]) == 0
    assert capfdbinary.readouterr().out == b"v1"


def test_help_names_every_argument(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["registry", "--help"])
    top = capsys.readouterr().out
    assert "ROOT" in top and "arg1" not in top
    for sub, needles in {
        "commit": ("NAME", "ARTIFACT", "--meta", "--business-date", "--safe", "--allow-raw"),
        "checkout": ("NAME", "REF", "-o"),
        "tag": ("TAG", "REF"),
        "promote": ("SOURCE", "TARGET"),
        "diff": ("REF1", "REF2"),
    }.items():
        with pytest.raises(SystemExit):
            main(["registry", "reg", sub, "--help"])
        text = capsys.readouterr().out
        assert "arg1" not in text and "arg2" not in text
        for n in needles:
            assert n in text, (sub, n)


def test_tag_and_promote_still_work(orders: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_commit()) == 0
    assert main(["registry", "reg", "tag", "orders", "v1"]) == 0
    assert main(["registry", "reg", "promote", "orders", "v1", "production"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "checkout", "orders", "production", "-o", "p.bin"]) == 0
    assert (orders / "p.bin").read_bytes() == b"v1"


def test_diff_of_two_raw_profiles_gives_the_drift(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = "\n".join(f"{i},user{i}@example.com,{50 + i % 40}" for i in range(500))
    (work / "customers2.csv").write_text("id,email,age\n" + rows + "\n")
    assert main(["profile", "customers2.csv", "-o", "cust2.shape"]) == 0
    for f in ("cust.shape", "cust2.shape"):
        assert main(["registry", "reg", "commit", "c", f, "--allow-raw"]) == 0
    capsys.readouterr()
    assert main(["registry", "reg", "diff", "c", "latest", "latest"]) == 0
    assert _json_out(capsys)["same"] is True  # type: ignore[index]
    first = LocalRegistry(work / "reg").log("c")[0]["content_id"]
    assert main(["registry", "reg", "diff", "c", first, "latest"]) == 0
    d = _json_out(capsys)
    assert d["same"] is False and d["drift"]["drifted"] is True  # type: ignore[index]

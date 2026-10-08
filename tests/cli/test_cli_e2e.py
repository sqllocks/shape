import json

from shape.cli.main import main


def test_capture_diff(tmp_path, capsys):
    p = tmp_path / "x.csv"
    p.write_text("x,s\n1,a\n2,b\n")
    o = tmp_path / "x.json"
    assert main(["capture", str(p), "-o", str(o)]) == 0
    assert json.loads(o.read_text())["rows"] == 2
    assert main(["diff", str(o), str(o)]) == 0 and json.loads(capsys.readouterr().out) == []


def test_conformance_cli(capsys):
    assert main(["conformance"]) == 0
    assert all(x["passed"] for x in json.loads(capsys.readouterr().out))

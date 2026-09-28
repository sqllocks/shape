import json

from shape.cli.main import main


def test_cli_doctor(capsys):
    assert main(["doctor"]) == 0
    o = json.loads(capsys.readouterr().out)
    assert "python" in o and "pyarrow" in o


def test_cli_key(tmp_path, capsys):
    p = tmp_path / "x.csv"
    p.write_text("id,x\n1,a\n2,b\n")
    assert main(["key", str(p), "id"]) == 0
    assert json.loads(capsys.readouterr().out)["unique"] is True

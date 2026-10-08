import json

from shape.cli.main import main


def test_generate(capsys):
    assert main(["generate", "--rows", "3", "--seed", "1"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 3


def test_validate_example(capsys):
    assert main(["validate", "examples/customer.shape.json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]

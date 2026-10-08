"""``shape.spec.load_contract`` and ``save_contract``: JSON and YAML round trips (AUD-tests)."""

import json

import pytest

from shape.spec import load_contract, save_contract

DOC = {
    "name": "orders",
    "version": 1,
    "fidelity": "gold",
    "fields": [],
    "metadata": {"owner": "data"},
}


@pytest.fixture
def contract(tmp_path):
    p = tmp_path / "in.json"
    p.write_text(json.dumps(DOC), encoding="utf-8")
    return load_contract(p)


@pytest.mark.parametrize("suffix", ["json", "yaml", "yml", "YAML"])
def test_a_saved_contract_loads_back_equal(contract, tmp_path, suffix):
    out = tmp_path / f"out.{suffix}"
    save_contract(contract, out)
    assert load_contract(out) == contract


def test_json_is_written_sorted_and_indented(contract, tmp_path):
    out = tmp_path / "out.json"
    save_contract(contract, out)
    text = out.read_text(encoding="utf-8")
    assert text == json.dumps(contract.to_dict(), indent=2, sort_keys=True)


def test_yaml_keeps_the_contract_key_order(contract, tmp_path):
    out = tmp_path / "out.yaml"
    save_contract(contract, out)
    keys = [line.split(":")[0] for line in out.read_text().splitlines() if not line[:1].isspace()]
    assert keys == list(contract.to_dict())


def test_an_unknown_suffix_is_read_as_json(tmp_path):
    p = tmp_path / "contract.txt"
    p.write_text(json.dumps(DOC), encoding="utf-8")
    assert load_contract(p).name == "orders"
    p.write_text("name: orders", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_contract(p)

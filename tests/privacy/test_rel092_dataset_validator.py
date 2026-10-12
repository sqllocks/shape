"""REL-092: sampling column metadata is not a dataset value dump."""

import copy
import json

import pytest

import shape
from shape.cli.main import main
from shape.privacy.safe_validator import SafeProfileValidator


@pytest.fixture
def dataset_capture(tmp_path):
    folder = tmp_path / "dataset"
    folder.mkdir()
    for name in ("orders", "accounts"):
        rows = ["amount,balance,units"]
        rows.extend(f"{i},{i * 2},{i * 3}" for i in range(2100))
        (folder / f"{name}.csv").write_text("\n".join(rows) + "\n")
    captured = tmp_path / "dataset.shape"
    assert main(["profile", str(folder), "--dataset", "-o", str(captured)]) == 0
    profile = shape.load(captured)
    document = profile.to_dict()
    document["capture"] = profile.capture
    document["redaction_manifest"] = profile.redaction_manifest
    return folder, captured, document


def test_clean_dataset_capture_passes_safe_cli(dataset_capture, tmp_path):
    _, captured, document = dataset_capture
    assert set(document["tables"]) == {"orders", "accounts"}
    assert any(
        len(entry.get("columns", [])) > 2
        for table in document["tables"].values()
        for entry in table["sampling"]["internal"]
    )
    path = tmp_path / "safe.json"
    path.write_text(json.dumps(document))
    assert main(["profile", "validate", "--safe", str(path)]) == 0
    assert main(["profile", "validate", "--safe", str(captured)]) == 0


def test_dataset_raw_sensitive_value_is_rejected(dataset_capture):
    document = copy.deepcopy(dataset_capture[2])
    document["leaked"] = "person@example.test"
    result = SafeProfileValidator().validate_data(document)
    assert not result.is_clean
    assert any(f.rule == "pii-regex" and f.path == "$.leaked" for f in result.findings)


def test_full_dataset_capture_is_rejected(dataset_capture, tmp_path, capsys):
    folder = dataset_capture[0]
    full = tmp_path / "full.shape"
    assert main(["profile", str(folder), "--dataset", "--capture", "full", "-o", str(full)]) == 0
    capsys.readouterr()
    assert main(["profile", "validate", "--safe", str(full), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "full-capture" in {finding["rule"] for finding in result["findings"]}


def test_leaking_table_payload_is_rejected(dataset_capture):
    document = copy.deepcopy(dataset_capture[2])
    document["tables"]["orders"]["columns"]["amount"]["payload"] = ["Alice", "Bob", "Carol"]
    result = SafeProfileValidator().validate_data(document)
    assert any(
        f.rule == "raw-string-list" and f.path.endswith("columns.amount.payload")
        for f in result.findings
    )


@pytest.mark.parametrize(
    "tables",
    [
        [],
        {},
        "not-a-table",
        {"orders": None},
        {"orders": {"row_count": False}},
        {"orders": {"row_count": 100, "columns": None}},
    ],
)
def test_malformed_dataset_is_rejected(dataset_capture, tables):
    document = copy.deepcopy(dataset_capture[2])
    document["tables"] = tables
    assert not SafeProfileValidator().validate_data(document).is_clean


@pytest.mark.parametrize(
    "mutation", ["unknown-column", "renamed-metadata", "nested-values", "pii-column"]
)
def test_sampling_exception_is_narrow_and_still_scans_values(dataset_capture, mutation):
    document = copy.deepcopy(dataset_capture[2])
    table = document["tables"]["orders"]
    entry = next(e for e in table["sampling"]["internal"] if len(e.get("columns", [])) > 2)
    if mutation == "unknown-column":
        entry["columns"] = ["Alice", "Bob", "Carol"]
    elif mutation == "renamed-metadata":
        table["renamed"] = table.pop("sampling")
    elif mutation == "nested-values":
        entry["payload"] = {"columns": ["Alice", "Bob", "Carol"]}
    else:
        table["columns"]["person@example.test"] = table["columns"].pop("amount")
        entry["columns"] = ["person@example.test", "balance", "units"]
    result = SafeProfileValidator().validate_data(document)
    assert not result.is_clean
    expected = "pii-regex" if mutation == "pii-column" else "raw-string-list"
    assert expected in {f.rule for f in result.findings}


def test_sampling_list_alias_does_not_exempt_a_table_payload(dataset_capture):
    document = copy.deepcopy(dataset_capture[2])
    table = document["tables"]["orders"]
    references = next(e["columns"] for e in table["sampling"]["internal"] if "columns" in e)
    table["payload"] = references
    result = SafeProfileValidator().validate_data(document)
    assert any(
        f.rule == "raw-string-list" and f.path.endswith("orders.payload") for f in result.findings
    )

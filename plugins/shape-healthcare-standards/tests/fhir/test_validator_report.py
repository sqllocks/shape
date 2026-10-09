"""The official validator report reader identifies sources independently of metadata order."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "official_validator", Path(__file__).with_name("test_official_validator.py")
)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

FILE_URL = "http://hl7.org/fhir/StructureDefinition/operationoutcome-file"
VERSION_URL = "http://hl7.org/fhir/tools/StructureDefinition/validator-version"


@pytest.mark.parametrize("version_first", [True, False])
def test_report_sources_ignore_validator_version(version_first):
    sources = ["member/bundle.json", "payer/payers.json"]
    entries = []
    for source in sources:
        extensions = [
            {"url": FILE_URL, "valueString": source},
            {"url": VERSION_URL, "valueString": "FHIR Validation tool Version 7.0.1"},
        ]
        if version_first:
            extensions.reverse()
        entries.append(
            {
                "resource": {
                    "extension": extensions,
                    "issue": [{"severity": "information", "details": {"text": "validated"}}],
                }
            }
        )
    assert validator._issues({"entry": entries}) == [
        (source, "information", "validated") for source in sources
    ]


@pytest.mark.parametrize(
    "extensions",
    [[], [{"url": FILE_URL, "valueString": "a"}, {"url": FILE_URL, "valueString": "b"}]],
)
def test_report_without_one_source_fails_closed(extensions):
    with pytest.raises(ValueError, match="source"):
        validator._issues({"entry": [{"resource": {"extension": extensions, "issue": []}}]})

"""Sampled capture uses the same disclosure rules as its observed rows."""

import pyarrow as pa
import pytest

import shape
from shape.profile.reference.profile import Profile


@pytest.mark.parametrize("sampled_rows", [0, 1, 2, 100])
def test_sample_support_matches_observed_rows_sensitivity(tmp_path, sampled_rows):
    values = list(range(sampled_rows))
    profile = shape.profile(pa.table({"amount": pa.array(values, type=pa.int64())}))
    from shape.privacy.redact import redact_profile

    observed = redact_profile(profile)
    data = profile.to_dict()
    data["row_count"] = 1000
    data["sampled_rows"] = sampled_rows
    profile = Profile(data)
    path = tmp_path / "sample.shape"
    shape.save(profile, path)
    loaded = shape.load(path)
    column = next(iter(loaded.tables.values()))["columns"]["amount"]
    expected = observed.tables["table"]["columns"]["amount"]
    assert column["min_value"] == expected["min_value"]
    assert column["max_value"] == expected["max_value"]
    assert loaded.redaction_manifest["tables"]["table"]["amount"]["sensitive"] == bool(sampled_rows)


def test_sample_support_does_not_disable_declared_sensitivity(tmp_path):
    profile = shape.profile(pa.table({"amount": [1, 2, 3, 4, 5]}))
    data = profile.to_dict()
    data["row_count"] = 1000
    data["sampled_rows"] = 2
    profile = Profile(data)
    path = tmp_path / "sensitive.shape"
    shape.save(profile, path, classifications={"amount": "CONFIDENTIAL"})
    loaded = shape.load(path)
    column = next(iter(loaded.tables.values()))["columns"]["amount"]
    assert column["min_value"] is None
    assert column["max_value"] is None

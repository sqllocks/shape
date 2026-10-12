"""The ``semantic-model://`` source and ``shape profile-model`` against a real semantic model
(only where the Fabric test settings exist; ``sempy`` must be able to sign in).

    FABRIC_WORKSPACE_ID        the workspace (name or GUID) that holds the model
    FABRIC_SEMANTIC_MODEL      the model (name or GUID); the live test profiles all of it

    pytest -m live plugins/shape-fabric/tests/test_live_semantic_model.py

A missing variable skips the test with the variable's name; service failures still fail.
"""

from __future__ import annotations

import os

import pytest
from shape_fabric import semantic_source as ss
from shape_fabric.semantic_profile import profile_model

pytestmark = pytest.mark.live


def need(name):
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"live test needs {name}")
    return value


def test_a_real_model_profiles_with_its_relationships_and_every_table_reads():
    workspace, model = need("FABRIC_WORKSPACE_ID"), need("FABRIC_SEMANTIC_MODEL")
    names = ss.table_names(workspace, model)
    assert names, "the model has no tables"
    data = profile_model(workspace, model, max_rows=1000).to_dict()
    assert list(data["tables"]) == names
    for r in data["relationships"]:
        assert r["evidence"] == "declared" and r["source"] == "semantic model"
    first = names[0]
    source = ss.SemanticModelSource()
    uri = ss.build(workspace, model, first)
    schema = source.schema(uri)
    batches = list(source.read(uri, max_rows=10))
    assert sum(b.num_rows for b in batches) <= 10
    assert all(b.schema.equals(schema) for b in batches)

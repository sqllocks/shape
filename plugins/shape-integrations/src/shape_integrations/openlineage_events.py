"""OpenLineage events built with the OpenLineage client (imports it: needs the extra)."""

from __future__ import annotations

import json
from typing import Any

import attr
from openlineage.client.event_v2 import Job, OutputDataset, Run, RunEvent, RunState
from openlineage.client.facet_v2 import schema_dataset
from openlineage.client.generated.base import RunFacet
from openlineage.client.serde import Serde

from .lineage import FACET_SCHEMA_URL, PRODUCER, Plan


@attr.define
class ShapeRunFacet(RunFacet):
    """The custom run facet ``shape``: the Shape run id, tuple and dataset id."""

    version: int = attr.field(kw_only=True)
    runId: str = attr.field(kw_only=True)  # noqa: N815
    engineVersion: str | None = attr.field(default=None, kw_only=True)  # noqa: N815
    reproducibility: dict[str, Any] | None = attr.field(default=None, kw_only=True)
    datasetId: str | None = attr.field(default=None, kw_only=True)  # noqa: N815

    @staticmethod
    def _get_schema() -> str:
        return FACET_SCHEMA_URL


def build(p: Plan) -> list[dict[str, Any]]:
    facet = ShapeRunFacet(
        version=p.facet["version"],
        runId=p.facet["runId"],
        engineVersion=p.facet.get("engineVersion"),
        reproducibility=p.facet.get("reproducibility"),
        datasetId=p.facet.get("datasetId"),
        producer=PRODUCER,
    )
    outputs = []
    for ds in p.outputs:
        facets: dict[str, Any] = {}
        if ds.columns:
            facets["schema"] = schema_dataset.SchemaDatasetFacet(
                fields=[
                    schema_dataset.SchemaDatasetFacetFields(name=n, type=t) for n, t in ds.columns
                ]
            )
        outputs.append(OutputDataset(namespace=p.namespace, name=ds.name, facets=facets))
    events = []
    for state, when in p.states:
        event = RunEvent(
            eventType=RunState[state],
            eventTime=when,
            run=Run(runId=p.run_uuid, facets={"shape": facet}),
            job=Job(namespace=p.namespace, name=p.job_name),
            producer=PRODUCER,
            inputs=[],
            outputs=outputs,
        )
        events.append(json.loads(Serde.to_json(event)))
    return events

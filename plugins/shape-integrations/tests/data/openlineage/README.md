Copies of the OpenLineage specification files that the lineage tests validate events against,
fetched unchanged from:

- https://openlineage.io/spec/2-0-2/OpenLineage.json
- https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json (1-2-0, the version the pinned client emits)

They are test data only, are not shipped in the wheel, and stay under the OpenLineage
project's own license (Apache-2.0). Tests never fetch them from the network.

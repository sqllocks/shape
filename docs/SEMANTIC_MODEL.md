# Semantic model profiles

Install `sqllocks-shape-fabric[semantic-link]` in a Fabric notebook. `sempy` signs in as the
notebook user; credentials are never URI components or profile fields.

```bash
shape profile-model Workspace/Model -o model.shape
shape profile semantic-model://Workspace/Model/Sales -o sales.shape
shape export-model --from-profile model.shape -o model.bim
shape known-answer model.shape --measures from-profile -o answers
```

A model profile adds a `measures` list to each table. Every measure records `name`, `expression`,
`formatString`, `displayFolder`, and `hidden`. It also adds dataset-level `roles`: each has `name`,
`modelPermission` and `tablePermissions` entries with `name` and `filterExpression`. Existing
profiles without these additive keys remain readable. The existing `.shape` format and version
stay unchanged. Measures are metadata, never numeric data columns.

Roles are read through `sempy.fabric.connect_semantic_model(dataset=..., workspace=...,
readonly=True)` and `tom.model.Roles`. This API and `list_measures` require the model permission
sempy documents as ReadWrite, even for this read-only connection. Shape never writes roles back
to a live model. TOM failures stop profiling rather than silently dropping security metadata.

For role-filtered rows, use the source API in the notebook:

```python
from shape_fabric.semantic_source import SemanticModelSource
source = SemanticModelSource()
batches = list(source.read("semantic-model://Workspace/Model/Sales", as_role="Region North"))
```

`as_role` is a nonempty role name. Shape validates it against TOM and passes `role=...` to
`sempy.fabric.evaluate_dax`; the engine applies the role's filters and their relationships. Unknown
roles are refused with available role names. Connection delimiters in a role name are refused.
`columns`, `batch_rows`, and `max_rows` still apply; a zero cap returns no rows after validation.
`mode` cannot be combined with `as_role`. Hidden columns remain readable and carry `hidden: true`
in both plain profiles and model profiles; visible columns omit that key.

`export-model --from-profile` takes a saved profile and preserves the exact DAX expressions,
format strings, display folders, hidden measure flags (TOM `isHidden`), and roles in its `.bim`.
It fits the table schema from the profile. Do not also pass a domain or `--mode`. `--no-measures`
omits measures while keeping roles. An older profile without semantic metadata exports empty
roles and no model-owned measures.

`known-answer MODEL.shape --measures from-profile` generates synthetic rows using the profile's
row counts, then computes its own measures. Supported complete expressions are `COUNTROWS('table')`
and `SUM`, `AVERAGE`, `MIN`, or `MAX` over a numeric column of any profiled table. Simple unquoted table names are accepted too. Quoted names use
DAX escaping (doubled table quotes and column closing brackets). Arbitrary DAX, references to absent tables/columns
and nonnumeric aggregates are recorded under `skipped` with `reason: "unsupported
DAX expression"`; their original expressions remain in `model.bim`. No DAX is executed locally.
Leave `--scale` out: the profile's row counts determine the scale. The existing `shape-dax-answers`
format/version 1 continues to apply.

`shape diff` reports added, removed and changed measures as `measure_change` and roles as
`role_change`, with old and new definitions and the object name. These metadata records have
score zero and are excluded from numeric drift gates. Numeric columns use the existing gates
and thresholds. Ordering alone in the measures/roles lists does not count as a change.

Object-level security, calculation groups, perspectives, translations and DirectLake-specific
behavior are outside this workflow. Live role/measure fixtures await the Owner's decision;
offline fake sempy tests verify the behavior on every PR.

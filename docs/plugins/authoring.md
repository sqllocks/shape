# Writing a Shape plugin

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](../contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" authoring
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for authoring
    ```


A plugin is an ordinary Python distribution that registers objects in one or more **entry-point
groups**. Shape finds it through the installed metadata; nothing in core changes. First-party
features use exactly the same route (see [builtins.md](builtins.md)).

Plugins are trusted, in-process code: the host checks that a plugin is compatible, not that it
is safe. Install only plugins you trust.

Read first: [api-v1.md](api-v1.md) (every Protocol) and [host.md](host.md) (how the host loads
plugins). A complete small plugin, with a source, a detector and a command, is in
[`examples/plugin/`](../../examples/plugin/).

## Start from a template

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

[Run this example](#local-example-0).


`shape plugins new NAME --group GROUP [-o DIR] [--author TEXT] [--dry-run]` writes a package that
works and conforms from the first minute: `pyproject.toml` with the entry point in `GROUP`, a module
that implements the group's Protocol from `shape.plugins.api.v1` and declares `SHAPE_API`, a test that
runs the conformance kit (`tests/test_conformance.py`, which also runs before the plugin is
installed), a README and a `.github/workflows/ci.yml` that runs the tests and
`python -m shape.plugins.kit NAME`. There is a template for every group (`shape.sources`,
`shape.sinks`, `shape.detectors`, and the rest of the table in section 2); the sample code does the
least that passes, and you replace its body.

`NAME` is lowercase letters, digits and single hyphens, starting with a letter, at most 48
characters; it is the distribution name and the entry-point name, and with hyphens as underscores the
Python package. An invalid name, an unknown group or a folder that is not empty exits with code 2 and
writes nothing. `--dry-run` lists the files without writing them. The sections below explain what
the generated files do; [`examples/plugin/`](../../examples/plugin/) stays as the worked example
with three plugins in one package.

## 1. The shape of a plugin

```
my-plugin/
  pyproject.toml
  src/my_plugin/__init__.py
  tests/test_conformance.py
```

```python
# src/my_plugin/__init__.py
SHAPE_API = "1.0"           # required: the host rejects a different major version

class IbanDetector:
    name = "iban"           # the registry key; lowercase letters, digits, "_" and "-"

    def detect(self, values, column):   # a pyarrow Array in, a Detection or None out
        ...
```

```toml
# pyproject.toml
[project]
name = "my-plugin"
version = "0.1.0"
dependencies = ["sqllocks-shape"]

[project.entry-points."shape.detectors"]
iban = "my_plugin:IbanDetector"
```

- The entry-point **name** must equal the object's `name`.
- The entry-point **value** names a class (instantiated with no arguments), a zero-argument
  callable that returns the object, or a ready object.
- The module that holds the entry point declares `SHAPE_API = "1.x"`. A plugin does not import
  or subclass the Protocols; it only has to match them structurally.
- Hooks take and return whole Arrow arrays and batches, never single values or rows.
- Be deterministic: the same input (and the same seed or context) gives the same output.
  Do not modify what you are given.
- A plugin that fails to import or build never affects core or other plugins. `shape plugins
  doctor` reports it, and `shape plugins list` shows what is installed.

## 2. The groups

| Group | You implement | Kit check | Sample you give the check |
|---|---|---|---|
| `shape.sources` | `Source` | `check_source` | `uri` the source can read |
| `shape.sinks` | `Sink` | `check_sink` | `uri`, `batches` (optionally `read_back`) |
| `shape.detectors` | `SemanticDetector` | `check_detector` | `positives`, `negatives` |
| `shape.fitters` | `DistributionFitter` | `check_fitter` | `sample` |
| `shape.strategies` | `Strategy` | `check_strategy` | `spec` |
| `shape.distributions` | `Distribution` | `check_distribution` | `params` |
| `shape.calendars` | `Calendar` | `check_calendar` | none (optional `start`, `end`) |
| `shape.domains` | `Domain` | `check_domain` | none |
| `shape.chaos` | `ChaosMutator` | `check_chaos` | `batch` |
| `shape.emitters` | `Emitter` | `check_emitter` | `uri`, `batches` |
| `shape.stream_sources` | `StreamSource` | `check_stream_source` | `uri` |
| `shape.transforms` | `Transform` | `check_transform` | `tables` |
| `shape.commands` | `Command` | `check_command` | `argv` (optional `expect_exit`) |
| `shape.reports` | `ReportFormat` | `check_report_format` | `report` |
| `shape.behaviors` | `Behavior` | `check_behavior` | none (optional `population`, `seed`, `years`) |

A behavior is a state-machine module for a simulator on a virtual clock
([behavior.md](behavior.md)): it has `name`, `version`, the `states` it uses, the `attributes` and
`events` it emits, and `simulate(population, seed, years)`, which returns the events as one Arrow
table. `check_behavior` runs a small population twice, so a behavior that is not deterministic
for a seed or emits an event or state it did not declare fails.

A command adds `shape <name>`: it has `name`, `help`, `configure(parser)` and `run(args)`, and
`run` returns the exit code. A built-in command always wins over a plugin command with the same
name.

## 3. Test it with the conformance kit

`shape.plugins.kit` has one check per Protocol. Call the check for your Protocol from any test
runner (the kit has no test-framework dependency); it raises `ConformanceError`, an
`AssertionError`, that names the first rule that broke:

```python
from shape.plugins import kit
from my_plugin import IbanDetector

def test_detector():
    kit.check_detector(
        IbanDetector(),
        positives=[["DE89370400440532013000", "GB29NWBK60161331926819"]],
        negatives=[["hello", "world"]],
    )
```

Each check covers what the Protocols and the host promise: the object implements its Protocol
and has a usable `name`; it returns the documented types; it is deterministic; it leaves its
inputs alone; and the group-specific rules (for example that a source only claims URIs it can
read, that a sink returns the number of rows written, that a stream source resumes right after
the offset it reports, that a calendar returns one non-negative factor per day).

Two more checks work on the plugin as a user gets it:

```python
kit.check_module_api(my_plugin)        # SHAPE_API is declared and its major version matches

kit.check_installed(                   # every shape.* entry point of an installed distribution:
    "my-plugin",                       # loaded through the host, then checked
    samples={"shape.detectors:iban": {"positives": [[...]]}},
)
```

or, from a shell, with no samples (the shared rules only):

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

[Run this example](#local-example-6).


`check_plugin(group, obj, **sample)` picks the check from a group name.

A strategy or distribution is keyed by `(seed, table, column, chunk)`. If your plugin promises
that its values do not depend on how rows are split into chunks, pass
`layout_independent=True` and the kit checks that one chunk equals two half chunks.

The kit tells you the plugin is well-formed. Whether it is *correct* (the right IBANs, the right
rows) is up to your own tests.

## 4. Install and try it

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

[Run this example](#local-example-7).


The acceptance test for the kit does exactly this for `examples/plugin/`: it installs the
example into a scratch directory outside the repository, runs the kit and the plugin's own
tests against it, and checks that `shape plugins list` shows it next to every built-in.

## 5. First-party plugins (monorepo)

Features that ship with Shape but are not part of core live under `plugins/<dist-name>/`, one
distribution each (decision T-09, with the additions of 2026-10-03): `shape-kafka`,
`shape-eventhubs`, `shape-fabric`, `shape-sqlserver`, `shape-databases`, `shape-domains`, `shape-simulation`,
`shape-dbt`, `shape-behavior`, `shape-healthcare-codes` and `shape-healthcare-standards`. They
publish as `sqllocks-shape-<name>` and are all MIT licensed. Install them with the extras
`pip install 'sqllocks-shape[fabric]'`, `pip install 'sqllocks-shape[dbt]'` and `pip install 'sqllocks-shape[healthcare]'` (the three
healthcare distributions), or one by one.

```
plugins/shape-kafka/
  pyproject.toml           name sqllocks-shape-kafka, version = core's, depends on sqllocks-shape==<that version>
  LICENSE                  the repository LICENSE
  README.md
  src/shape_kafka/__init__.py      SHAPE_API = "1.0"
  tests/                   uses shape.plugins.kit
```

Each one starts as a **skeleton**: it builds and installs, declares `SHAPE_API` and registers
nothing. The work package that implements a plugin adds its entry points to `pyproject.toml`,
its code under `src/`, and kit-based tests. `shape-sqlserver` is the first one implemented; its
guide is [sqlserver.md](sqlserver.md). `shape-kafka` and `shape-eventhubs` follow it; their guide
is [streaming.md](streaming.md). The guides of the later four are [DBT.md](../DBT.md),
[behavior.md](behavior.md), [healthcare-codes.md](healthcare-codes.md) and the README of
`plugins/shape-healthcare-standards` (X12, FHIR and OMOP writers).
`shape-integrations` (OpenLineage, MLflow, Presidio, SDMetrics, Anonymeter, Ibis and DuckDB, one
extra each) is described in [integrations.md](integrations.md).

Rules that `python scripts/check_plugin_skeletons.py` enforces (and CI runs):

- every T-09 distribution exists, named `sqllocks-shape-<name>`;
- its version equals core's, and it depends on exactly that core version (lockstep: one
  release, atomic API changes);
- it ships core's `LICENSE`, and a package that declares `SHAPE_API`;
- any entry-point group it declares is a real plugin API group;
- with `--build OUT`, each one builds a pure-Python `py3-none-any` wheel.

When core's version changes, change all eleven `pyproject.toml` files in the same commit; the
script fails until they match.

## 6. Versioning

`SHAPE_API` is `"MAJOR.MINOR"`. The host loads a plugin whose major version equals its own
(`shape.plugins.api.v1.SHAPE_API`), so a plugin written for API 1.0 keeps loading on every 1.x
release, and a future 2.0 host reports a clear error for it instead of misbehaving.

What is stable within 1.x, what counts as a breaking change, and the deprecation process are
in [stability.md](stability.md).

A strategy or distribution may also declare `generator_version` (an integer, default 1), the version
of its algorithm; it is optional and additive in API v1, so a plugin that does not declare it keeps
loading and counts as version 1. A plugin that raises it keeps the older versions selectable
(`generate_versioned` / `sample_versioned`) so specs that pin them keep their data. See
[GENERATION_STABILITY.md](../GENERATION_STABILITY.md).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape plugins new acme-iban --group shape.detectors
cd acme-iban
pip install --quiet --no-deps --no-build-isolation -e .
python -m pytest -q
python -m shape.plugins.kit acme-iban
```

??? info "Output (exit 0)"

    ```text {.expected}
    {
      "name": "acme-iban",
      "group": "shape.detectors",
      "folder": "acme-iban",
      "files": [
        "acme-iban/pyproject.toml",
        "acme-iban/README.md",
        "acme-iban/src/acme_iban/__init__.py",
        "acme-iban/tests/conftest.py",
        "acme-iban/tests/test_conformance.py",
        "acme-iban/.github/workflows/ci.yml"
      ]
    }
    ..                                                                                           [100%]
    2 passed in 0.55s
    shape.detectors:acme-iban: ok (shared rules only: no sample given)
    OK acme-iban: 1 plugin(s) conform to plugin API 1.0
    ```

<a id="local-example-6"></a>

### Example 7

<!-- example: 6 -->

```bash {.runnable-reference}
python -m shape.plugins.kit sqllocks-shape-dbt
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape.commands:dbt-report: ok (shared rules only: no sample given)
    shape.commands:dbt-seeds: ok (shared rules only: no sample given)
    shape.commands:from-dbt: ok (shared rules only: no sample given)
    shape.commands:to-dbt-tests: ok (shared rules only: no sample given)
    shape.sinks:dbt-seeds: ok (shared rules only: no sample given)
    OK sqllocks-shape-dbt: 5 plugin(s) conform to plugin API 1.0
    ```

<a id="local-example-7"></a>

### Example 8

<!-- example: 7 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e acme-iban
shape plugins list --group shape.detectors
shape plugins info shape.detectors:iban
shape plugins doctor
```

??? info "Output (exit 0)"

    ```text {.expected}
    12 plugin(s)
      shape.detectors:cpt        unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:email      unloaded [sqllocks-shape]
      shape.detectors:hcpcs      unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:icd10      unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:ipv4       unloaded [sqllocks-shape]
      shape.detectors:mbi        unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:member_id  unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:ndc        unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:npi        unloaded [sqllocks-shape-healthcare-codes]
      shape.detectors:phone      unloaded [sqllocks-shape]
      shape.detectors:presidio   unloaded [sqllocks-shape-integrations]
      shape.detectors:us_ssn     unloaded [sqllocks-shape]
    shape: error: no plugin 'shape.detectors:iban' (see `shape plugins list`)
    plugin API 1.0: 179 plugin(s)
      ok    shape.behaviors:entity_lifecycle [sqllocks-shape-behavior]
      ok    shape.behaviors:equipment_maintenance [sqllocks-shape-behavior]
      ok    shape.behaviors:event_sequence [sqllocks-shape-behavior]
      ok    shape.behaviors:file_arrival [sqllocks-shape-behavior]
      ok    shape.behaviors:healthcare_screening [sqllocks-shape-behavior]
      ok    shape.behaviors:subscription [sqllocks-shape-behavior]
      ok    shape.behaviors:telemetry_series [sqllocks-shape-behavior]
      ok    shape.behaviors:transaction_stream [sqllocks-shape-behavior]
      ok    shape.calendars:composite [sqllocks-shape]
      ok    shape.calendars:us_federal [sqllocks-shape]
      ok    shape.calendars:us_retail [sqllocks-shape]
      ok    shape.chaos:file [sqllocks-shape]
      ok    shape.chaos:referential [sqllocks-shape]
      ok    shape.chaos:schema [sqllocks-shape]
      ok    shape.chaos:temporal [sqllocks-shape]
      ok    shape.chaos:value [sqllocks-shape]
      ok    shape.chaos:volume [sqllocks-shape]
      ok    shape.commands:behave [sqllocks-shape-behavior]
      ok    shape.commands:check-answers [sqllocks-shape-fabric]
      ok    shape.commands:ctgan [sqllocks-shape]
      ok    shape.commands:dbt-report [sqllocks-shape-dbt]
      ok    shape.commands:dbt-seeds [sqllocks-shape-dbt]
      ok    shape.commands:deploy-notebook [sqllocks-shape-fabric]
      ok    shape.commands:evaluate [sqllocks-shape-integrations]
      ok    shape.commands:export-model [sqllocks-shape-fabric]
      ok    shape.commands:fabric [sqllocks-shape-fabric]
      ok    shape.commands:from-dbt [sqllocks-shape-dbt]
      ok    shape.commands:healthcare-codes [sqllocks-shape-healthcare-codes]
      ok    shape.commands:known-answer [sqllocks-shape-fabric]
      ok    shape.commands:lineage [sqllocks-shape-integrations]
      ok    shape.commands:mlflow [sqllocks-shape-integrations]
      ok    shape.commands:notebook [sqllocks-shape-fabric]
      ok    shape.commands:profile-db [sqllocks-shape-sqlserver]
      ok    shape.commands:profile-model [sqllocks-shape-fabric]
      ok    shape.commands:publish [sqllocks-shape-fabric]
      ok    shape.commands:publish-report [sqllocks-shape-fabric]
      ok    shape.commands:setup-fabric [sqllocks-shape-fabric]
      ok    shape.commands:simulate [sqllocks-shape-simulation]
      ok    shape.commands:to-dbt-tests [sqllocks-shape-dbt]
      ok    shape.detectors:cpt [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:email [sqllocks-shape]
      ok    shape.detectors:hcpcs [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:icd10 [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:ipv4 [sqllocks-shape]
      ok    shape.detectors:mbi [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:member_id [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:ndc [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:npi [sqllocks-shape-healthcare-codes]
      ok    shape.detectors:phone [sqllocks-shape]
      ok    shape.detectors:presidio [sqllocks-shape-integrations]
      ok    shape.detectors:us_ssn [sqllocks-shape]
      ok    shape.distributions:bernoulli [sqllocks-shape]
      ok    shape.distributions:beta [sqllocks-shape]
      ok    shape.distributions:exponential [sqllocks-shape]
      ok    shape.distributions:gamma [sqllocks-shape]
      ok    shape.distributions:geometric [sqllocks-shape]
      ok    shape.distributions:histogram [sqllocks-shape]
      ok    shape.distributions:log_normal [sqllocks-shape]
      ok    shape.distributions:lognormal [sqllocks-shape]
      ok    shape.distributions:mixture [sqllocks-shape]
      ok    shape.distributions:negative_binomial [sqllocks-shape]
      ok    shape.distributions:normal [sqllocks-shape]
      ok    shape.distributions:pareto [sqllocks-shape]
      ok    shape.distributions:poisson [sqllocks-shape]
      ok    shape.distributions:power_law_cutoff [sqllocks-shape]
      ok    shape.distributions:triangular [sqllocks-shape]
      ok    shape.distributions:truncated [sqllocks-shape]
      ok    shape.distributions:uniform [sqllocks-shape]
      ok    shape.distributions:weibull [sqllocks-shape]
      ok    shape.distributions:zipf [sqllocks-shape]
      ok    shape.domains:capital_markets [sqllocks-shape-domains]
      ok    shape.domains:education [sqllocks-shape-domains]
      ok    shape.domains:financial [sqllocks-shape-domains]
      ok    shape.domains:healthcare [sqllocks-shape-domains]
      ok    shape.domains:hr [sqllocks-shape-domains]
      ok    shape.domains:insurance [sqllocks-shape-domains]
      ok    shape.domains:iot [sqllocks-shape-domains]
      ok    shape.domains:manufacturing [sqllocks-shape-domains]
      ok    shape.domains:marketing [sqllocks-shape-domains]
      ok    shape.domains:pulse [sqllocks-shape-domains]
      ok    shape.domains:real_estate [sqllocks-shape-domains]
      ok    shape.domains:retail [sqllocks-shape-domains]
      ok    shape.domains:supply_chain [sqllocks-shape-domains]
      ok    shape.domains:telecom [sqllocks-shape-domains]
      ok    shape.emitters:console [sqllocks-shape]
      ok    shape.emitters:eventhouse [sqllocks-shape-fabric]
      ok    shape.emitters:eventhubs [sqllocks-shape-eventhubs]
      ok    shape.emitters:eventstream [sqllocks-shape-fabric]
      ok    shape.emitters:fhir [sqllocks-shape-healthcare-standards]
      ok    shape.emitters:file [sqllocks-shape]
      ok    shape.emitters:jsonl [sqllocks-shape]
      ok    shape.emitters:kafka [sqllocks-shape-kafka]
      ok    shape.fitters:auto [sqllocks-shape]
      ok    shape.reports:html [sqllocks-shape]
      ok    shape.reports:json [sqllocks-shape]
      ok    shape.reports:md [sqllocks-shape]
      ok    shape.sinks:abfss [sqllocks-shape]
      ok    shape.sinks:csv [sqllocks-shape]
      ok    shape.sinks:databricks [sqllocks-shape-databases]
      ok    shape.sinks:dbt-seeds [sqllocks-shape-dbt]
      ok    shape.sinks:delta [sqllocks-shape]
      ok    shape.sinks:duckdb [sqllocks-shape-databases]
      ok    shape.sinks:excel [sqllocks-shape]
      ok    shape.sinks:fabric-mirror [sqllocks-shape]
      ok    shape.sinks:fhir-bundle [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:fhir-ndjson [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:ipc [sqllocks-shape]
      ok    shape.sinks:jsonl [sqllocks-shape]
      ok    shape.sinks:mysql [sqllocks-shape-databases]
      ok    shape.sinks:ncpdp [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:omop [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:parquet [sqllocks-shape]
      ok    shape.sinks:postgres [sqllocks-shape-databases]
      ok    shape.sinks:snowflake [sqllocks-shape-databases]
      ok    shape.sinks:sql [sqllocks-shape]
      ok    shape.sinks:sqlserver [sqllocks-shape-fabric]
      ok    shape.sinks:synapse [sqllocks-shape-fabric]
      ok    shape.sinks:tsv [sqllocks-shape]
      ok    shape.sinks:warehouse [sqllocks-shape-fabric]
      ok    shape.sinks:x12-277ca [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:x12-834 [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:x12-835 [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:x12-837i [sqllocks-shape-healthcare-standards]
      ok    shape.sinks:x12-837p [sqllocks-shape-healthcare-standards]
      ok    shape.sources:abfss [sqllocks-shape]
      ok    shape.sources:csv [sqllocks-shape]
      ok    shape.sources:delta [sqllocks-shape]
      ok    shape.sources:duckdb [sqllocks-shape-integrations]
      ok    shape.sources:hl7v2 [sqllocks-shape-healthcare-standards]
      ok    shape.sources:ipc [sqllocks-shape]
      ok    shape.sources:json [sqllocks-shape]
      ok    shape.sources:jsonl [sqllocks-shape]
      ok    shape.sources:mssql [sqllocks-shape-sqlserver]
      ok    shape.sources:onelake [sqllocks-shape-fabric]
      ok    shape.sources:parquet [sqllocks-shape]
      ok    shape.sources:semantic-model [sqllocks-shape-fabric]
      ok    shape.sources:x12 [sqllocks-shape-healthcare-standards]
      ok    shape.sources:xml [sqllocks-shape]
      ok    shape.strategies:address [sqllocks-shape]
      ok    shape.strategies:bootstrap [sqllocks-shape]
      ok    shape.strategies:choice [sqllocks-shape]
      ok    shape.strategies:composite_fk_field [sqllocks-shape]
      ok    shape.strategies:composite_foreign_key [sqllocks-shape]
      ok    shape.strategies:computed [sqllocks-shape]
      ok    shape.strategies:conditional [sqllocks-shape]
      ok    shape.strategies:conditional_table [sqllocks-shape]
      ok    shape.strategies:constant [sqllocks-shape]
      ok    shape.strategies:correlated [sqllocks-shape]
      ok    shape.strategies:derived [sqllocks-shape]
      ok    shape.strategies:distribution [sqllocks-shape]
      ok    shape.strategies:empirical [sqllocks-shape]
      ok    shape.strategies:faker [sqllocks-shape]
      ok    shape.strategies:first_per_parent [sqllocks-shape]
      ok    shape.strategies:foreign_key [sqllocks-shape]
      ok    shape.strategies:formula [sqllocks-shape]
      ok    shape.strategies:hierarchy [sqllocks-shape]
      ok    shape.strategies:hierarchy_field [sqllocks-shape]
      ok    shape.strategies:lifecycle [sqllocks-shape]
      ok    shape.strategies:locale [sqllocks-shape]
      ok    shape.strategies:lookup [sqllocks-shape]
      ok    shape.strategies:native [sqllocks-shape]
      ok    shape.strategies:normal [sqllocks-shape]
      ok    shape.strategies:pattern [sqllocks-shape]
      ok    shape.strategies:record_field [sqllocks-shape]
      ok    shape.strategies:record_sample [sqllocks-shape]
      ok    shape.strategies:reference_data [sqllocks-shape]
      ok    shape.strategies:scd2 [sqllocks-shape]
      ok    shape.strategies:self_ref_field [sqllocks-shape]
      ok    shape.strategies:self_referencing [sqllocks-shape]
      ok    shape.strategies:sequence [sqllocks-shape]
      ok    shape.strategies:temporal [sqllocks-shape]
      ok    shape.strategies:uniform [sqllocks-shape]
      ok    shape.strategies:uuid [sqllocks-shape]
      ok    shape.strategies:weighted_enum [sqllocks-shape]
      ok    shape.stream_sources:eventhubs [sqllocks-shape-eventhubs]
      ok    shape.stream_sources:kafka [sqllocks-shape-kafka]
      ok    shape.transforms:cdm [sqllocks-shape]
      ok    shape.transforms:mask [sqllocks-shape]
      ok    shape.transforms:star [sqllocks-shape]
    all plugins load
    ```

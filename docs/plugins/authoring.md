# Writing a Shape plugin

A plugin is an ordinary Python distribution that registers objects in one or more **entry-point
groups**. Shape finds it through the installed metadata; nothing in core changes. First-party
features use exactly the same route (see [builtins.md](builtins.md)).

Plugins are trusted, in-process code: the host checks that a plugin is compatible, not that it
is safe. Install only plugins you trust.

Read first: [api-v1.md](api-v1.md) (every Protocol) and [host.md](host.md) (how the host loads
plugins). A complete small plugin, with a source, a detector and a command, is in
[`examples/plugin/`](../../examples/plugin/).

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

```bash
python -m shape.plugins.kit my-plugin
```

`check_plugin(group, obj, **sample)` picks the check from a group name.

A strategy or distribution is keyed by `(seed, table, column, chunk)`. If your plugin promises
that its values do not depend on how rows are split into chunks, pass
`layout_independent=True` and the kit checks that one chunk equals two half chunks.

The kit tells you the plugin is well-formed. Whether it is *correct* (the right IBANs, the right
rows) is up to your own tests.

## 4. Install and try it

```bash
pip install -e .
shape plugins list --group shape.detectors
shape plugins info shape.detectors:iban
shape plugins doctor
```

The acceptance test for the kit does exactly this for `examples/plugin/`: it installs the
example into a scratch directory outside the repository, runs the kit and the plugin's own
tests against it, and checks that `shape plugins list` shows it next to every built-in.

## 5. First-party plugins (monorepo)

Features that ship with Shape but are not part of core live under `plugins/<dist-name>/`, one
distribution each (decision T-09): `shape-kafka`, `shape-eventhubs`, `shape-fabric`,
`shape-sqlserver`, `shape-domains`, `shape-simulation` and `shape-mcp`. They publish as
`sqllocks-shape-<name>`.

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
is [streaming.md](streaming.md).

Rules that `python scripts/check_plugin_skeletons.py` enforces (and CI runs):

- every T-09 distribution exists, named `sqllocks-shape-<name>`;
- its version equals core's, and it depends on exactly that core version (lockstep: one
  release, atomic API changes);
- it ships core's `LICENSE`, and a package that declares `SHAPE_API`;
- any entry-point group it declares is a real plugin API group;
- with `--build OUT`, each one builds a pure-Python `py3-none-any` wheel.

When core's version changes, change all seven `pyproject.toml` files in the same commit; the
script fails until they match.

## 6. Versioning

`SHAPE_API` is `"MAJOR.MINOR"`. The host loads a plugin whose major version equals its own
(`shape.plugins.api.v1.SHAPE_API`), so a plugin written for API 1.0 keeps loading on every 1.x
release, and a future 2.0 host reports a clear error for it instead of misbehaving.

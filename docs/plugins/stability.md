# Plugin API v1: stability promise

Status: available (early-access surface); coming (1.x policy).

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

The compatibility promises below apply to the future 1.x line, not the current early-access release.

This page is the promise Shape makes to plugin authors about plugin API v1. The Protocols
themselves are in [api-v1.md](api-v1.md) (generated from the code); how to write a plugin is in
[authoring.md](authoring.md). Every claim below is enforced by a test in this repository
(`tests/plugins/test_stability_promise.py`, `tests/plugins/test_api_v1_compat.py`).

## What is stable in 1.x

Within the 1.x series, a plugin written against API 1.0 keeps working, unchanged, on every later
1.x release of Shape. That covers, for each entry-point group below:

- the **entry-point group name** and the Protocol it maps to;
- the Protocol's **members**: each attribute (`name`, and `schemes`, `families`, `help` or
  `extension` where the Protocol has them) with its type, and each method with its parameter names,
  order, kinds, defaults and return type;
- the **data types** a plugin of that group receives or returns (`Detection`, `FitResult`,
  `GenerationContext`, `DomainDefinition`, `ChaosReport`, `StreamOffset`): field names, order, types
  and frozen-ness;
- the **rules the conformance kit checks** for that group (determinism, row counts, inputs left
  untouched, and so on);
- how the host **discovers, loads and versions** a plugin: the `SHAPE_API` declaration below and
  the entry-point factory convention (a class or zero-argument callable returning an object that
  implements the Protocol).

Plugins are trusted, in-process code (see [trust-model.md](trust-model.md)); the promise is about
the interface, not about sandboxing.

| Entry-point group | Protocol | Conformance check |
|---|---|---|
| `shape.sources` | `Source` | `check_source` |
| `shape.sinks` | `Sink` | `check_sink` |
| `shape.detectors` | `SemanticDetector` | `check_detector` |
| `shape.fitters` | `DistributionFitter` | `check_fitter` |
| `shape.strategies` | `Strategy` | `check_strategy` |
| `shape.distributions` | `Distribution` | `check_distribution` |
| `shape.calendars` | `Calendar` | `check_calendar` |
| `shape.domains` | `Domain` | `check_domain` |
| `shape.chaos` | `ChaosMutator` | `check_chaos` |
| `shape.emitters` | `Emitter` | `check_emitter` |
| `shape.stream_sources` | `StreamSource` | `check_stream_source` |
| `shape.transforms` | `Transform` | `check_transform` |
| `shape.commands` | `Command` | `check_command` |
| `shape.reports` | `ReportFormat` | `check_report_format` |
| `shape.behaviors` | `Behavior` | `check_behavior` |

The Protocols are in `shape.plugins.api.v1`; the checks are in `shape.plugins.kit`.

## What counts as a breaking change

A change is breaking if a plugin that conforms to API 1.0 could stop loading, stop conforming or
stop working because of it. Breaking changes are allowed only with a new **major version** of the
plugin API (a 2.0 host reports a clear error for a 1.x plugin instead of misbehaving). These are
breaking:

- removing or renaming an entry-point group, a Protocol, a Protocol member, a data type or a field;
- adding a **required** member to a Protocol (an attribute or method every plugin must now have),
  because existing plugins would no longer satisfy it. An optional capability gets its own new
  Protocol instead;
- changing an attribute's type, a method's return type, or a parameter's name, position, kind,
  type or default;
- adding a parameter to a Protocol method without a default (or that is not `*args`/`**options`),
  or a data-type field without a default;
- removing or reordering existing parameters or fields, or making a frozen data type mutable;
- tightening what the kit checks so that a plugin that passed on 1.0 now fails;
- changing what the host accepts for `SHAPE_API` so that a 1.x declaration is rejected on a 1.x
  host.

`python scripts/plugin_api_compat.py --check` compares the live Protocols and data types with the
committed baseline `tests/plugins/api_v1_baseline.json` (API 1.0 as shipped) and lists each
breaking change; the compatibility test for every group runs it. The baseline is refreshed with
`--write` only for additive changes, and `--write` refuses a breaking one.

## What is not a breaking change

Within 1.x, Shape may:

- add a new entry-point group, with its Protocol, kit check and compatibility test;
- add a new optional Protocol, an optional parameter with a default, or a data-type field with a
  default;
- add checks to the kit that a *correct* 1.0 plugin already satisfies, and fix kit bugs that
  rejected a correct plugin;
- add new first-party plugins, change the behaviour of built-ins, and change anything not listed
  under "What is stable in 1.x", including the host's logging, `shape plugins` output wording and
  private modules (anything with a leading underscore, and anything outside `shape.plugins.api.v1`
  and `shape.plugins.kit`);
- raise the minor version in `SHAPE_API` (see below).

## Deprecation process

Nothing in the 1.x series is removed. To retire something, Shape:

1. marks it as deprecated: the word "Deprecated" with the replacement in its docstring in
   `shape.plugins.api.v1`, and a row in the table below naming it, the release that deprecated it
   and what to use instead;
2. announces it in the release notes of that release;
3. keeps it working, with the same behaviour, for the rest of the 1.x series. It remains in the
   Protocols, the baseline and the kit, and a plugin that uses it still passes its check;
4. removes it only in the next major version of the plugin API.

A test fails if a docstring says "Deprecated" and the table does not list the member, or the
table lists a member the code does not mark, so the table is the complete list.

| Member | Deprecated in | Use instead |
|---|---|---|

Nothing is deprecated in API 1.0.

## Declaring the API version

A plugin declares the plugin API it targets with a module-level string in the module that holds
its entry point:

```python
SHAPE_API = "1.0"   # "MAJOR.MINOR"
```

- The host loads a plugin whose **major** version equals its own (`1`) and accepts any minor
  (`"1.0"`, `"1.3"`, `"1.99"`). A plugin that declares `"2.0"`, `"0.9"` or something that is not
  `MAJOR.MINOR`, or that declares nothing, is not loaded; `shape plugins doctor` shows the reason.
- Declare the lowest minor whose features you use. The minor number records which additions a
  plugin knows about; it is not a negotiation, and the host does not refuse a plugin because its
  minor is newer than the host's.
- The declaration is checked by the kit (`check_module_api`) and by the host at load time.
- A new minor is published only for additive changes, and its additions are listed in the release
  notes.

## Stable plugin options

Options of a first-party plugin that other tools build on are stable in the same way. The
**`tables` option** of the healthcare standards writers (the `shape.sinks` entry points `x12-837p`,
`x12-837i`, `x12-835`, `x12-834`, `fhir-ndjson`, `fhir-bundle`, `omop` and `ncpdp`, and the `fhir`
emitter) is documented in
[healthcare-standards.md](healthcare-standards.md#companion-tables-tables) and is stable within
1.x: its **name**, its **accepted types** (a mapping from contract table name to `pyarrow.Table` or
`pyarrow.RecordBatch`; `None` or absent for none), its **precedence rule** (the primary table
replaces a companion of the same name) and its **error class** (`ContractError`) do not change.
Adding a newly accepted contract table or a newly read optional table is additive. Removing or
renaming the option, changing the precedence or changing the error class is breaking and follows
the deprecation process below. `plugins/shape-healthcare-standards/tests/test_companion_tables_contract.py`
pins this.

## Groups added later

Plugin API v1 gains groups over time. A group added in a 1.x release is under this same promise
from the release that adds it, and is added together with all of the following (tests fail
until each is in place):

1. its entry in `GROUPS` and `PROTOCOLS` in `shape.plugins.api.v1` (the generated
   [api-v1.md](api-v1.md) follows);
2. a row in the group table of `stability.md` (this page);
3. its entry, with the data types it uses, in `tests/plugins/api_v1_baseline.json` (via
   `python scripts/plugin_api_compat.py --write`) and in `GROUP_TYPES` in that script;
4. its check in `CHECKS` in `shape.plugins.kit`;
5. a frozen reference plugin and a compatibility test case for it in
   `tests/plugins/test_api_v1_compat.py`.

From then on, a change to that group's Protocol is judged by the rules above.

## Checking your plugin

Run the conformance kit in your plugin's own CI. There is one check per group (see the table); each
raises `ConformanceError` naming the first rule your plugin broke:

```python
from shape.plugins import kit

def test_my_detector():
    kit.check_module_api("my_plugin")                     # SHAPE_API declared, major matches
    kit.check_plugin("shape.detectors", MyDetector(), positives=[["a@b.co"]])
```

To check everything an installed distribution registers, through the same host that loads it at
runtime:

Use [the tested starters](../TUTORIAL.md) for local commands and complete output.

<!-- example: 2 -->

Syntax reference. Replace the named arguments with your inputs.

```text
python -m shape.plugins.kit my-plugin [--samples my_plugin.samples:SAMPLES]
```


It exits 0 when every plugin conforms, 1 when one does not, and 2 for a usage error. `check_installed`
does the same from Python. Pin the Shape version range you test against, for example
`sqllocks-shape>=1,<2`, and run the kit on the oldest and newest 1.x you support: because of this
promise, a kit failure on a newer 1.x is a bug to report to Shape, not a change you must absorb.

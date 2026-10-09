# Scenario packs and generation specs

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A **scenario pack** is a YAML file that bundles a domain, a simulation kind, optional chaos,
validation gates and the landing paths of a run. A **generation spec** (GSL, `*.gsl.yaml`) points at
a pack and sets the schema, scale, seed, chaos, outputs and gates around it. Both run with
`shape pack`:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Shape ships **no packs**, but it does ship a library of named starter scenarios with answer keys:
`shape pack list --library` lists them and `shape pack run library:NAME` runs one and checks the
outcome against its key (`docs/SCENARIO_LIBRARY.md`; `shape suite run` runs a set of them).
`library:NAME` also works with `shape pack validate`. `--scale` (a preset of the scenario's domain,
or `tiny`), `--seed`, `-o` and `--json` apply; `--root` and `--domain` do not (exit 2), since a
library scenario has its own domain. Exit 0 when the scenario met its key (a gate that fails where
the key says it must is met), 1 when not, 2 for an unknown scenario. For your own packs: write one
(below) or keep a directory of your own, laid out as
`<root>/<domain>/<id>.yaml` if you want `shape pack run DOMAIN/ID --root <root>`. Reading YAML
needs PyYAML (`pip install 'sqllocks-shape[yaml]'`).

## A pack

```yaml
version: 1                 # the format version (`pack_version: 1` is still read)
id: my_custom_pack
kind: file_drop            # file_drop | stream | hybrid
domain: retail
description: Custom daily batch for demo

fabric_targets:
  lakehouse_files_root: Files/landing/retail   # relative to the output directory

file_drop:
  cadence: daily
  partitioning: dt=YYYY-MM-DD
  formats: [parquet]       # the first one is written: parquet, csv, jsonl (json = jsonl)
  entities: [customer, order]
  manifest: {enabled: true}
  done_flag: {enabled: true}
  lateness: {enabled: false}
  duplicates: {enabled: false}

validation:
  required_gates: [schema_conformance]
```

| Kind | What a run writes |
|---|---|
| `file_drop` | the `entities` (every table when none are listed), one file each, under `fabric_targets.lakehouse_files_root` |
| `stream` | one `<topic>_<event_type>.jsonl` per topic, holding the rows of the table the topic names (the same name, else the first table whose name contains the topic's or is contained in it) |
| `hybrid` | `micro_batch/<entity>.<format>` files plus `stream_<topic>_<event_type>.jsonl` events |

The timing fields (cadence, partitioning, rates, jitter, replay, done flags, `lateness`,
`duplicates`, `backfill`, `failure_injection`) describe the landing pattern for a consumer. A run
does not simulate a clock: it writes the data once. To produce that behaviour (late arrivals,
duplicates, a backfill, replays, an event rate) use the simulators of `sqllocks-shape-simulation`
(`shape simulate`, `docs/SIMULATION_FILES_EVENTS.md`), which write through the same file sinks. Validation warns when `lateness`, `duplicates`,
`backfill` or `failure_injection` is enabled, because nothing would happen. Use `chaos` for
faults.

### Chaos

```yaml
chaos:
  enabled: true
  intensity: stormy        # calm | moderate | stormy | hurricane
  escalation: gradual      # gradual | random | front-loaded
  warmup_days: 7
  breaking_change_day: 20
  day: 25                  # optional: the day the run represents
```

The run is one day (`day`, else the later of the chaos start and the breaking-change day).
`shape.chaos` mutates every generated table (schema, value, temporal, volume), then the tables
together (referential, once), deterministically for a seed (`seed`, default 42). The manifest's
`chaos` counts what changed per category (for `volume`, the rows added or removed; a category that
changed nothing is not listed). Gates run on the mutated tables, so a failing gate is the
result chaos is for: the run still exits 0 and the summary shows the gate as FAIL.

### Gates

`schema_conformance`, `referential_integrity`, `row_count`, `null_check` and `uniqueness` run over
the final tables. A gate that is not in this list fails (validation warns about it first). Without
chaos, a failing gate makes the run fail (exit 1).

## A generation spec

```yaml
version: 1
name: retail_daily_demo

schema:
  type: domain             # domain | schema_file
  domain: retail           # for schema_file: path: schema.json (as `shape from-ddl` writes)

scenario:
  pack: my_pack.yaml       # relative to the spec file
  scale: fabric_demo
  seed: 42
  date_range: {start: "2025-01-01", end: "2025-01-31"}   # descriptive
  identifiers: reserved    # optional: reserved (default) or realistic

chaos:
  enabled: true
  intensity: stormy
  config: {warmup_days: 7, escalation: gradual, breaking_change_day: 20}

outputs:
  lakehouse:
    mode: files_only       # tables_and_files | tables_only | files_only
    tables: [customer, order]            # replaces the pack's entities
    landing_zone: {root: Files/landing/retail}   # replaces lakehouse_files_root
  eventstream:
    enabled: false         # a spec run does not send events: use `shape emit`

validation:
  gates: [schema_conformance, referential_integrity]   # replaces the pack's gates
  drift_policy: quarantine_on_breaking_change
```

`shape pack run SPEC` takes scale and seed from the spec; `--scale` and `--seed` override them. The
manifest's `spec_hash` is the SHA-256 of the spec file. `scenario.identifiers` is the run switch of
the identifier values (`reserved` or `realistic`, `docs/GENERATION_STRATEGIES.md`): `--identifiers`
overrides it, and without either the schema's `"identifiers"` applies, then `reserved`. Any other
value is refused before anything is generated. The mode the run used is recorded in the manifest
(`reproducibility.identifiers`).

## The run manifest

Every run writes `<run_id>_manifest.json` into the output directory
(`YYYYMMDD_HHMMSS_{domain}_{scale}_s{seed}`; a run in the same second gets an `_x2` suffix).
A domain name that is a path (a separator, `..`, a drive or `:`) is refused before anything is
generated or written (`unsafe domain name '...': it must be a plain name, not a path`); a scale
name is made plain in the run id (anything but letters, digits, `.`, `_` and `-` becomes `_`).

| Key | Content |
|---|---|
| `run_id`, `pack_id`, `domain`, `scale`, `seed` | identity of the run |
| `spec_hash` | SHA-256 of the generation spec file, empty without one |
| `engine_version` | the Shape version |
| `outputs` | `kind`, number of `files` and `events`, `root` |
| `tables` | per generated table: `rows`, `columns`, `file_paths` (exactly its own files) |
| `validation` | gate name to pass/fail |
| `chaos` | category to the number of changes |
| `timestamps` | `started`, `finished`, `elapsed_seconds` |
| `workspace_id`, `lakehouse_id` | Fabric identifiers, empty unless set |
| `format`, `version` | `shape-run-manifest`, `1` (a manifest from a newer Shape is refused; one without them loads with an empty `reproducibility` and `dataset_id`) |
| `shape_version`, `min_shape_version` | the Shape release that wrote the manifest, and the first release that reads its `version` (`docs/specs/STATE_AND_COMPATIBILITY.md`) |
| `reproducibility` | the reproducibility tuple: `schema_version`, `profile_version`, `seed`, `scale`, `shape_version`, `kernel`, `platform` (`docs/REPRODUCIBILITY.md`), plus `identifiers` (the run's identifier mode; a manifest without it was a reserved run) |
| `dataset_id` | the content address of the output tables (`sha256:...`), over every generated table after chaos |
| `sbom` | version of `sqllocks-shape`, `pandas`, `numpy`, `faker`, `pyarrow` and `scipy` (`not installed` when absent) |

`shape pack replay MANIFEST TARGET` regenerates a run from its manifest and checks the dataset id
(`docs/REPRODUCIBILITY.md`).

## Safety

A pack is data, not code, but it names paths. Validation and the runner refuse a
`lakehouse_files_root` or a spec landing root that is absolute, has a drive or contains `..`, a
topic or event type that is not one plain file name, and a landing directory that resolves
(through a symbolic link) outside the output directory. A pack file that is empty, not a mapping or
has a value of the wrong type or a key twice in one mapping is an error that names the key. Keys no
field takes are warned about, so a mistyped key does not silently change nothing. A run manifest
with a field of the wrong type is refused the same way when `replay` reads it.

## From Python

```python
from shape.generation.domains import load_domain
from shape.scenario import PackLoader, PackRunner, PackValidator

pack = PackLoader().load("my_pack.yaml")
domain = load_domain(pack.domain)
assert PackValidator().validate(pack, domain).is_valid
result = PackRunner().run(pack, domain, scale="fabric_demo", seed=42, base_path="out")
print(result.summary(), result.manifest.to_dict()["tables"])
```

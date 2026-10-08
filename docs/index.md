# Shape by SQLLocks

**Shape as Code:** a portable, executable description of how data behaves, not merely its
schema. Shape profiles your data into a `.shape` file, checks data against contracts, reports
drift between two shapes, and generates realistic synthetic data from a shape or a schema.

Install with `pip install sqllocks-shape`. Python imports use `import shape`, the command is
`shape`, and artifacts use the `.shape` extension.

```python
import shape

p = shape.profile("customers.csv")      # also: Parquet, JSONL, Delta tables, pandas, pyarrow
shape.save(p, "customers.shape")
print(p.summary())                      # small JSON-safe summary per column

result = shape.check(p, {"columns": {"customer_id": {"unique": True, "nullable": False}}})
print(result.passed, result.violations)
```

```bash
shape profile customers.csv -o customers.shape --name customers --html report.html
shape check customers.shape contract.json          # exit code 1 if the contract fails
shape diff customers.shape customers_next.shape --fail-on-drift
```

## Where to start

| If you want to | Read |
|---|---|
| install Shape and its optional extras | [Install](INSTALL.md) |
| try it in five minutes | [Quickstart](QUICKSTART.md), then the [tutorial](TUTORIAL.md) |
| profile files, folders and tables | [File sources](SOURCES.md) and [profiling notes](PROFILING_NOTES.md) |
| catch drift and enforce contracts | [Drift](DRIFT.md) and [verifying data](VERIFY.md) |
| generate synthetic data | [Schema design](DESIGN.md) and the [generation engine](GENERATION_ENGINE.md) |
| run Shape in Fabric and Azure | [Fabric commands](plugins/fabric-commands.md) and [Fabric writers](plugins/fabric-writers.md) |
| share a profile safely | [Privacy model](PRIVACY_MODEL.md) and the [safe-to-share bundle](SHARE_BUNDLE.md) |
| look up a command or a function | [Command line](CLI.md) and [Python API](API.md) |
| write a plugin | [Writing a plugin](plugins/authoring.md) and [Plugin API v1](plugins/api-v1.md) |

## Status

Shape is in early access. What is stable, and what that promise covers, is set out in
[CLI stability](CLI_STABILITY.md), [API stability](API_STABILITY.md) and the
[release policy](RELEASE_POLICY.md). Shape is open source under the MIT license.

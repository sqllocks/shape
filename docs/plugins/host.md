# Plugin host

`shape.plugins.host` discovers, checks, loads and registers plugins. Plugins are trusted,
in-process code; the host checks compatibility, not safety. The API a plugin implements is
in [api-v1.md](api-v1.md).

## How a plugin is declared

A distribution lists entry points in one of the groups of API v1 (`shape.sources`,
`shape.detectors`, and so on). The value points at a class, a zero-argument callable, or a
ready object that implements the group's Protocol. The module must declare `SHAPE_API = "1.x"`.

```toml
[project.entry-points."shape.detectors"]
iban = "my_plugin.detectors:IbanDetector"
```

## What the host does

1. **Discovery** reads entry-point metadata only. It imports no plugin code.
2. **Loading** is lazy, on the first `get`: import the module, require a `SHAPE_API` whose
   major version matches `shape.plugins.api.v1.SHAPE_API`, resolve the entry point, build the
   object (a class is instantiated; a callable is called; an object that already satisfies
   the Protocol is used as it is), and check it against the group's Protocol.
3. **Failure containment:** any exception (including `SystemExit`) raised while importing or
   building a plugin is stored on its record (`status == "error"`, `error` text). It never
   escapes discovery, `names`, `records` or `load_all`. A failed plugin is not retried
   until `reload()`. Core features and other plugins are unaffected.
4. **Duplicates:** if two distributions register the same name in a group, the first
   (sorted by group, name, entry-point value) wins and the other is reported as an error.
   Unknown groups are ignored.

## Stable surface (used by P2-03, P2-04 and P2-06)

| Call | Purpose |
|---|---|
| `default_host()` | The process-wide `PluginHost`. `reset_default_host()` drops it (tests). |
| `PluginHost(entry_points=None)` | A host; pass a callable returning `EntryPoint`s to replace installed-package discovery. |
| `host.records(group=None)` | Every `PluginRecord`, sorted; never imports a plugin. |
| `host.names(group)` | Names registered in a group. |
| `host.record(group, name)` | One `PluginRecord` or `None`. |
| `host.get(group, name)` | The loaded object. `KeyError` if unknown, `PluginLoadError` if it failed, `PluginBlockedError` (a `PluginLoadError`) if the allow-list refuses it. |
| `host.try_get(group, name)` | Like `get`, but `None` on either error. |
| `host.load_all(group=None)` | Load everything, record failures, return the records. |
| `host.register(group, name, obj, api=..., source=...)` | Register an object or factory without an entry point (built-ins, tests). The same API and Protocol checks run at load. |
| `host.reload()` | Forget everything and discover again. |
| `PluginHost(entry_points=None, allowlist=None, project_config=None)` | `allowlist` is a path to a plugin allow-list; else `SHAPE_PLUGIN_ALLOWLIST`, else `plugins.allowlist` in `project_config` (the parsed `shape.yml`). See [trust-model.md](trust-model.md#the-plugin-allow-list). |
| `host.allowlist_active`, `host.allowlist` | Whether an allow-list is in force, and the parsed list. |
| `PluginRecord` | `group`, `name`, `target`, `source`, `status` (`unloaded`, `ok`, `error`, `blocked`), `api`, `error` (the reason when blocked), `blocked_kind`, `obj`; `as_dict()` is JSON-safe. |
| `plugins.doctor.diagnose(host)`, `format_report(report)` | The report behind `shape plugins doctor`. |

## The `shape plugins` commands

| Command | What it does | Exit |
|---|---|---|
| `shape plugins list [--group G] [--json]` | One line per registered plugin (group, name, status, distribution). Reads metadata only; imports no plugin. | 0 |
| `shape plugins info [GROUP:]NAME [--json]` | Loads one plugin and prints its group, Protocol, distribution, target, declared API, error (if any) and docstring. A bare name that exists in several groups is ambiguous. | 0 ok, 1 failed to load, 2 unknown or ambiguous |
| `shape plugins doctor [--json]` | Loads every plugin and prints one line per plugin; plugins the allow-list refused are listed separately and not imported. | 0 all load, 1 any failed or a listed plugin failed its version, file or signature check |
| `shape plugins allowlist init [-o PATH] [--pin-hashes] [--json]` | Writes an allow-list for the plugins installed now; imports none; never overwrites. | 0, 2 if the file exists |
| `shape plugins sign WHEEL --key KEY [-o OUT]` | Adds `shape-plugin.sig` to a wheel and updates its `RECORD`. Needs the `[sign]` extra. | 0, 2 on a bad wheel or key |
| `shape plugins verify DIST_OR_WHEEL [--key PUBLIC.pub] [--json]` | Checks a plugin's signature and files. | 0 valid, 1 missing or invalid, 2 usage error |

With an allow-list in force `shape plugins list` also shows `allowed` or `blocked -- reason` (and `--json` has `allowed` and `reason`). Details, the file format and what the checks do not cover: [trust-model.md](trust-model.md).

`shape profile` and the other commands never load plugins they do not use.

## Plugin commands

An entry point in `shape.commands` adds `shape <name>`. The object has `name`, `help`,
`configure(parser)` (adds arguments to the command's own argparse parser) and `run(args)`
(returns the exit code). `shape --help` lists installed commands by name without importing
them; the plugin loads only when `shape <name>` runs. A built-in command always wins over a
plugin command of the same name. A command that fails to load, or raises, prints one error
line to stderr and exits 1 (one the allow-list blocks exits 2); argument errors exit 2.

A complete example (a source, a detector and a command) is in `examples/plugin/`; how to
write and test a plugin is in [authoring.md](authoring.md).

`--json` on `doctor` prints the report from `plugins.doctor.diagnose`.

## Built-ins

Core's own sources, sinks, detectors, fitters, strategies, distributions and calendars register
through this host too; see [builtins.md](builtins.md). `default_host()` adds any of them that
entry-point discovery did not find (`shape.plugins.registry.register_builtins`).

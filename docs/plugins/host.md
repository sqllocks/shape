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
3. **Failure isolation:** any exception (including `SystemExit`) raised while importing or
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
| `host.get(group, name)` | The loaded object. `KeyError` if unknown, `PluginLoadError` if it failed. |
| `host.try_get(group, name)` | Like `get`, but `None` on either error. |
| `host.load_all(group=None)` | Load everything, record failures, return the records. |
| `host.register(group, name, obj, api=..., source=...)` | Register an object or factory without an entry point (built-ins, tests). The same API and Protocol checks run at load. |
| `host.reload()` | Forget everything and discover again. |
| `PluginRecord` | `group`, `name`, `target`, `source`, `status` (`unloaded`, `ok`, `error`), `api`, `error`, `obj`; `as_dict()` is JSON-safe. |
| `plugins.doctor.diagnose(host)`, `format_report(report)` | The report behind `shape plugins doctor`. |

## `shape plugins doctor`

Loads every plugin and prints one line per plugin; `--json` prints the report. Exit code 0 when
every plugin loads, 1 when any failed. `shape profile` and the other commands never load
plugins they do not use.

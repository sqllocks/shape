# CLI stability: the promise for 1.x

This page is the promise Shape makes to people who call `shape` from scripts and CI pipelines.
The exit codes are in [CLI.md](CLI.md); the promises for the `.shape` format and for plugins are
in [API_STABILITY.md](API_STABILITY.md) and [plugins/stability.md](plugins/stability.md). What
"1.0 is done" means is in [V1_DONE.md](V1_DONE.md). Every claim below is enforced by a check in
this repository, named in [Enforcement](#enforcement).

## Which commands are stable

A command is **stable** or **experimental**. Everything not listed as stable is experimental, and
each experimental command says so: its `--help` starts with `(experimental)`, and so does its line
in `shape --help`.

| Level | Commands |
|---|---|
| Stable | `capture`, `cat`, `check`, `compatibility`, `describe`, `diff`, `doctor`, `drift`, `generate`, `git-setup`, `inspect`, `keygen`, `list`, `pack`, `plan`, `plugins`, `presets`, `profile`, `registry`, `show`, `sign`, `validate`, `verify`, `version` |
| Experimental | every other core command, for example `bridge`, `chaos`, `demo`, `emit`, `fidelity`, `jobs`, `learn`, `mask`, `proposals`, `stream`, `transform` (`shape --help` lists them all) |

An experimental command may change or go away in any minor release, without the deprecation
process below. It is promoted to stable by adding it to the table above and to
`shape.cli.stability.STABLE`, and nothing is promoted without a release note. A stable command is
never demoted within 1.x.

Commands that a plugin adds (`shape.commands`) are covered by the plugin promise, not by this one.

## What is stable for a stable command

Within the 1.x series, a script written against 1.0 keeps working on every later 1.x release. For
a stable command that covers:

- its **name**, and the names of its **subcommands** (`shape pack replay`);
- its **flag names** (every spelling, long and short) and **what each means**, whether a flag takes
  a value, and the values it accepts when it has a fixed list of choices;
- its **positional arguments**: their order and number;
- the **exit-code classes** of [CLI.md](CLI.md): 0 ok; 1 a check failed; 2 bad input; 3 and above
  a command's own verdict. A command keeps the class of each outcome it has;
- the **keys of its `--json` output**: a key keeps its name and meaning.

What is not covered: the wording of messages (except the deprecation warning below), the layout of
human-readable output, the order of keys in JSON, the exact exit code within "3 and above" for a
verdict a command did not document, and timing.

## What may change in a minor release

Additions: a new command or subcommand; a new flag; a new value for a flag with choices; a new
optional positional; a new key in `--json` output; a new outcome with its own exit code of 3 or
above; a flag that was a no-op starting to do something. A script must not break because
output gained a key, so parse JSON by key, not by position.

## What counts as a breaking change

A breaking change needs a new major version, unless it went through the deprecation process:

- removing or renaming a stable command, subcommand or flag (any spelling);
- a flag that took a value taking none, or the reverse; removing a choice; accepting fewer values
  than before;
- removing a positional, changing the order of the positionals, or adding a required one;
- changing the meaning of a flag or of an exit-code class, or moving an outcome to another class
  (a failed check that exits 2, a bad input that exits 1);
- removing or renaming a `--json` key, or changing its type;
- demoting a stable command to experimental.

A bug fix that makes a command do what its documentation already said is not a breaking change; it
is listed in `CHANGELOG.md`.

## Deprecation process

To retire a stable command, subcommand, flag or choice, Shape:

1. keeps it working, with the same behaviour, for **at least one minor release** after the release
   that deprecates it;
2. prints on stderr, each time it is used:

   ```
   shape: warning: --OLD is deprecated and will be removed in X.Y; use --NEW
   ```

   (for a command, `shape: warning: OLD is deprecated and will be removed in X.Y; use NEW`), where
   X.Y is the release that removes it. The warning does not change the exit code;
3. lists it in `CHANGELOG.md`, in the release that deprecates it and in the release that removes it;
4. records it in `shape.cli.stability.DEPRECATIONS` and in the table below. Removing something that
   is in that table does not fail the surface check; removing anything else does.

| Deprecated | Deprecated in | Removed in | Use instead |
|---|---|---|---|

Nothing is deprecated in CLI 1.0.

## Enforcement

- `python scripts/cli_surface.py --check` (part of `make check`) compares the live parser with
  `tests/cli/cli_surface_v1.json` (`format: "shape-cli-surface"`, `version: 1`), which holds every
  core command and subcommand with its stability level, flags (names, whether each takes a value,
  choices) and positionals. It exits 0 when the parser is compatible, 1 when a stable command,
  flag or choice was removed or renamed without a deprecation entry (naming each one), and 2 on a
  usage error. Additions pass and are reported. `python scripts/cli_surface.py --write` refreshes
  the baseline after an additive change and refuses a breaking one.
- `tests/cli/test_cli_surface.py` tests the check itself (a removed flag exits 1 and is named, an
  added flag exits 0) and that every experimental command says so in its `--help`.
- `tests/cli/test_exit_code_classes.py` runs one representative command per exit-code class.
- The keys of each command's `--json` output are covered by that command's own tests. A snapshot
  of the keys, with the `--json` and `--dry-run` coverage of every command, is work package W1-14;
  until it lands, the `--json` promise is enforced command by command, not by one check.

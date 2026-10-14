# W-syn-4 lane status

Status: stopped on a documentation/code contradiction, as required by the lane prompt.

Base: `origin/main` at the commit recorded below. Only this status file changes. No production code, tests, reference corpus, public documentation, release files, plan file, or labels were changed. No live dispatch, promotion, PR, or merge was performed.

## Finding and exact question

The Synapse option table in `docs/plugins/fabric-writers.md`, in the `synapse://` section, documents 13 keys:

`chunk_rows`, `columns`, `connection`, `connection_string`, `copy_identity`, `credential`, `distribution`, `index`, `primary_key`, `schema`, `schema_name`, `staging_path`, `write_mode`.

`SynapseSink._write` in `plugins/shape-fabric/src/shape_fabric/sinks.py` additionally consumes `filesystem` and `commit_rows`. `SynapseSink.write` calls `_prefixed_table`, which additionally consumes `table_prefix`. Thus the code accepts 16 keys and the documented table lists 13. The table contains no unsupported keys. `filesystem` is a dependency injection seam, while `commit_rows` is validated as a positive integer and `table_prefix` is the shared physical table prefix.

**Exact question:** Should W-syn-4 pin all 16 accepted Synapse sink options, with the documentation owner adding `commit_rows`, `filesystem`, and `table_prefix` to the option table, or should the test explicitly exclude any of those accepted options from the documented `--sink-config` surface? If exclusions are intended, which keys and what rule should detect future undocumented accepted keys? The lane is tests-only, so it cannot resolve the missing documentation itself.

## Red evidence

A Python 3.12 diagnostic parses the option table's first column, collects literal `opts.pop(...)` keys and the `_take(...)` tuple in `SynapseSink`, and includes `_prefixed_table`'s consumed key. Its equality assertion failed (exit 1):

```text
Documented: chunk_rows, columns, connection, connection_string, copy_identity, credential, distribution, index, primary_key, schema, schema_name, staging_path, write_mode
Accepted but undocumented: commit_rows, filesystem, table_prefix
Documented but not accepted:
AssertionError: Synapse documented keys differ from accepted keys
```

This is diagnostic evidence, not a committed failing test. The prompt requires stopping instead of guessing when the plan contradicts the code or is ambiguous.

## Commands and exits

- Read `/workspace/prompt-runs/W-syn-4.md` and inspected original remote: 0.
- Same-shell `cd /workspace/shape && git remote get-url origin`: 0; confirmed `https://github.com/sqllocks/shape.git` before clone/setup work.
- Read original `git config user.name` and `git config user.email`: 0; copied the configured identity to the independent clone.
- `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-syn-4`: 0.
- Set cloned origin to the original GitHub URL and copied Git identity: 0.
- `git fetch origin`: 0.
- `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`: 0.
- `git checkout -B lane/W-syn-4 origin/main`: 0.
- `cat AGENTS.md CLAUDE.md CONTRIBUTING.md`: 1 because AGENTS.md is absent; CLAUDE.md and CONTRIBUTING.md were read. The surrounding inspection shell finished 0.
- `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` to an untracked temporary file outside the clone: 0; read Decisions and section 4.7. The plan was not merged or committed.
- Repository/plan searches with `rg` and targeted `sed`/`head` reads: 0, except `rg -n 'table_prefix' docs/plugins/fabric-writers.md`: 1 (no matches).
- Search for AGENTS.md across workspace: 0; none applies to this clone.
- Inspected original virtual environment and native build directory: 0. No dependencies, shared build targets, or other lane files were modified.
- Python 3.12 AST/Markdown equality diagnostic: 1, as shown above.
- Status-file creation: 0.

Bootstrap, Ruff, mypy, neighboring tests, and `make check` were not run: execution stopped on the explicit contradiction rule before changing tests or setting up dependencies. Python 3.11 is unavailable in the supplied environment; Python 3.12 would be used if work resumes. No CHANGELOG change was guessed before resolving the stop question.

Commit and push use only `lane/W-syn-4`; the lead records the resulting SHA and remote verification.
a8e476ca910c40e3d8e7dd0dace9c80699237e82

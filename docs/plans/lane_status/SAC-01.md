# SAC-01 — Shape as Code (issue #1) — status

Branch `lane/SAC-01` (from `build/main-plan` at 7e54b90). Status: **done, pending lead verification.**
No D-xx/T-xx decision, gate or tolerance was touched; nothing to escalate under §0.4. §11 and §2.3 are unedited.

## What was built
1. **Byte-reproducible container** (`src/shape/artifact/io.py`, `signing.py`). `write_container`
   writes `manifest.json`, components sorted by name, then `manifest.sig`; every member has the
   timestamp 1980-01-01, creator system Unix, mode 0644, no extra and no comment. `sign_artifact`
   rebuilds through the same writer, so a signed file is as reproducible as an unsigned one and
   re-signing an old file normalizes it.
   - **Judgment call: members are stored, not deflated.** Deflate output depends on the zlib build
     (zlib vs zlib-ng), so deflate cannot give identical bytes across machines; only `ZIP_STORED`
     can. git compresses and delta-packs the bytes itself, so repository size is not worse.
     Readers accept every method, and old deflated artifacts still read (tested). The lead may
     override this; the change is the constant in `write_zip_member`/`write_container`.
   - Signature input is unchanged: Ed25519 over the domain prefix plus the exact manifest bytes,
     independent of member order, timestamps and compression (tested by re-wrapping the same
     manifest and signature in a differently built zip, which still verifies).
   - Encrypted artifacts: the only encryption in the tree is `secure.py`'s `SecureEnvelope`, a
     JSON payload with a random AES-GCM nonce, not a zip container and not a `.shape` writer.
     It is random by design and was not changed.
2. **Readable diff** (`src/shape/cli/gitcmds.py`, wired by one parser call and one dispatch block
   in `main.py`). `shape cat FILE` prints one `path: value` line per property, keys sorted, no
   content hashes or signature (`--json`: sorted indented JSON). It sniffs zip content, because
   git hands a textconv filter an extension-less temp file. `shape inspect --pretty` prints the
   same document as indented sorted JSON. `shape git-setup [--repo DIR] [--pattern GLOB]...
   [--command CMD]` sets `diff.shape.textconv` in the repo-local config and adds `*.shape
   diff=shape` to `.gitattributes`, idempotently. The "semantic" form is the flat line form: each
   changed property is one line carrying its path. It does not call `shape diff`; use that for
   judged drift. Safe JSON: `to_json` now also sorts keys (it already had a trailing newline,
   fixed indent and no volatile field); `*.safe.json` can be diffed through `shape cat` with
   `--pattern`.
3. **Stable name.** `shape profile --name NAME`; without it, `-o` over an existing profile keeps
   that profile's name, otherwise the input's name (documented in `--help` and the README).
   The Python API already had `shape.profile(src, name=...)`; no API change was needed.
4. **Committed artifact (lead decision kept).** `--json` summary content is unchanged. The README
   already said the `.shape`, HTML report and JSON summary "as you would the source data", so no
   doc promised `--json` is safe: no reason found to reverse the decision. The README now says
   `--json` and the raw `.shape` hold real values and are pipeline-internal, and that
   `shape profile safe` output, checked by `shape profile validate --safe`, is what to commit.
5. **Docs and CI.** README "Version-controlling shapes"; SHAPE_ARTIFACT_SPEC writer convention 9
   (additive, rules 1-8 unchanged). Tests: `tests/artifact/test_container_reproducible.py` (6),
   `tests/cli/test_shape_as_code.py` (7, real git in a temp repo).

## Issue acceptance criteria
| Criterion | Evidence |
|---|---|
| Re-profile unchanged data, `git status` clean, raw `.shape` and safe JSON, separate processes | `test_git_workflow_end_to_end` (commit, sleep 2.1 s, re-profile in new processes with different `PYTHONHASHSEED`, `git status --porcelain` empty); `test_two_processes_write_equal_bytes`. "Machines": fixed fields + stored members; not run on another OS here. |
| `git diff` shows one changed line per changed property, path visible | same e2e test: `-columns.email.null_rate: …` / `+columns.email.null_rate: …`, `+columns.status.enum_values.lost: …`, no "Binary files", no name line |
| `shape git-setup` configures a repo in one command | `test_git_setup_is_idempotent_and_local`, `..._outside_a_repository_exits_2` |
| No git artifact has real values, `validate --safe` | e2e: `example.com` absent from `orders.safe.json`, `shape profile validate --safe` exit 0 |
| Artifact spec rules hold | signing input independent of container (test above); fail-closed readers: `tests/artifact` + `tests/security` pass; content ids unchanged (they hash `shape.json`, not the zip) |

## Checks (run in this session; `origin/build/main-plan` had no new commits, so no merge was needed)
| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | All checks passed / 689 files formatted |
| `python -m mypy` (strict) | Success: no issues found in 305 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no output |
| `lint-imports` | 1 kept, 0 broken |
| `python scripts/check_user_facing.py` / `--wheel` (built wheel) | clean / clean |
| `bandit -q -r src -ll` | no findings (only nosec warnings) |
| START (`shape --version`, 7 runs) | median 54 ms, max 68 ms (limit 300) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 4443 passed, 46 deselected |
| same with `SHAPE_KERNEL=python` | 4443 passed, 46 deselected |
| `pytest tests/demo --ignore=tests/demo/fabric` | 133 passed |
| `verify.py --impl shape`, `SHAPE_KERNEL=rust` and `python`, pinned baseline at 422e78d (cloned read-only, unmodified) | both exit 0 |

Environment note: `pyspark` would not build with the Debian setuptools; installed with
`pip install --ignore-installed setuptools wheel` and `--no-build-isolation`.

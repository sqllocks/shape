# The pull request check: `uses: sqllocks/shape@<tag>`

Status: experimental.

A composite GitHub Action that profiles the sources of a Shape project, compares each with its
baseline, posts the result as **one comment** on the pull request and fails the job when the result
says so. Make the job a required status check and it gates the merge.

```yaml
name: shape
on: [pull_request]          # the pull_request event (see Fork pull requests)
permissions:
  contents: read
  pull-requests: write      # for the comment
jobs:
  shape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sqllocks/shape@v1.0.0
        with:
          fail-on: drift
```

It needs a project (`shape.yml`, [PROJECT.md](PROJECT.md)) whose sources have a baseline
(`shape init` writes one to start from). The action runs, per source:

<!-- example: 1 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape profile NAME -o current/NAME.shape
shape diff --source NAME current/NAME.shape --json - --junit NAME.junit.xml --sarif NAME.sarif
```


then `shape ci comment` over the result documents (`shape-result`, [CI.md](CI.md)), the comment is
appended to the job's step summary (`$GITHUB_STEP_SUMMARY`), `shape ci post-comment` puts it on the
pull request, and the last step ends with the exit status that `fail-on` selects. The JUnit and SARIF
reports stay on the runner, in the folder of `comment-path`, where `actions/upload-artifact` or
`github/codeql-action/upload-sarif` can take them.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `shape-version` | the action's own tag | The Shape release to install (`1.0.0`; a leading `v` is dropped). When the ref after `@` is not a release number (a branch), the latest release is installed with a notice. `installed` installs nothing and uses the Shape already on the runner (the action's own self-test does this). Anything else is refused. |
| `project` | `.` | The directory that holds `shape.yml`. |
| `sources` | all | Source names to check, separated by commas or spaces. A name that is not a valid source name stops the action with exit 2. |
| `fail-on` | `fail` | When the job fails: `fail`, `drift` or `never` (see below). |
| `comment` | `true` | Post the pull request comment (`true` or `false`). The step summary is written either way. |
| `token` | `${{ github.token }}` | The token that writes the comment. |
| `python-version` | `3.12` | The Python the action runs Shape with. |

## Outputs

| Output | Meaning |
|---|---|
| `verdict` | `pass`, `drift` or `fail`: the worst verdict of the checked sources. |
| `findings` | The number of findings over all sources. |
| `comment-path` | The Markdown comment on the runner. |

## Verdicts and `fail-on`

A source's verdict follows the exit code of its `shape diff`:

- `fail`: the command exited with anything but 0 (drift with `--fail-on-drift`, or a command that
  could not run: a missing baseline, an unreadable source).
- `drift`: it exited 0 and still reported findings.
- `pass`: it exited 0 and reported none. A change that a registered planned change covers
  is listed apart and is not a finding.

`fail-on` selects what ends the job with exit status 1:

| `fail-on` | `shape diff` runs with | The job fails when the verdict is |
|---|---|---|
| `fail` | no gate on drift | `fail` (the command could not run, or the project's own gates failed) |
| `drift` | `--fail-on-drift` | `fail`, which is what drift becomes under that flag |
| `never` | no gate on drift | never; the verdict and the comment are still produced |

So `fail-on: fail` reports drift (amber) without blocking, and `fail-on: drift` blocks on any drift,
as the workflow `shape init` writes does. An empty or unknown verdict never passes the gate.

## Gating the merge

The check is a job, so it is gated by the usual rule: in the repository's branch protection (or a
ruleset), require the status check named after the job (`shape` in the example above). The comment
tells reviewers what changed; the required check stops the merge. Nothing else (the comment, the
badge, a notification) can block a merge.

## The comment

`shape ci comment RESULT.json... [-o FILE] [--max-findings 50] [--title TEXT]` renders it; the action
calls it with the defaults. It holds a hidden marker line `<!-- shape-pr-comment -->`, the verdict, one
section per source with a table of findings (table, column, kind, severity) and a footer with the
Shape version. Planned changes have their own table. Findings past `--max-findings` are counted
(`... and 12 more findings not shown`). The text is the same for the same inputs.

- It reads only names from a result document (source, table, column, kind, severity, plan id), so a
  value that safe capture suppressed, a baseline or a current value never appears in it. A command
  that failed shows its exit code, not its error text.
- Names are escaped: Markdown and HTML syntax in a column name, and an `@name`, render as text and
  never as a link, a tag or a mention.

`shape ci post-comment --body-file FILE --repo OWNER/REPO --pr N` puts it on the pull request:

- The token is read from `GITHUB_TOKEN` and used only as the `Authorization` header. It is never an
  argument, and never in an output or an error message.
- The server is `https://api.github.com`, or `GITHUB_API_URL` (GitHub Enterprise Server sets it,
  usually with a path such as `/api/v3`). HTTPS only; plain HTTP is accepted for a loopback address
  (the tests). A redirect is not followed.
- An existing comment **by the same author that carries the marker** is updated, otherwise one is
  created: a pull request has exactly one Shape comment however often the check runs. The author is
  the token's user, or `github-actions[bot]` for the workflow token, which cannot read `/user`. A
  comment of someone else that copies the marker is left alone.
- A 403 or 404 prints a notice and exits 0 (see Fork pull requests); any other HTTP error, or a
  server that cannot be reached, exits 1. In the action a failed post is a warning: the check result
  decides the gate.
- `--dry-run` prints one `send` action (`github OWNER/REPO#N`) and connects to nothing.

## Token permissions

The default `${{ github.token }}` needs `pull-requests: write` for the comment (the workflow's
`permissions:` key) and `contents: read` for the checkout. With `comment: false` it needs neither and
the action only reads. Pass a token of a GitHub App or a bot account as `token` to have the comment
come from that identity.

## Fork pull requests

On a `pull_request` event from a fork the token is read-only, so the comment cannot be written. The
action then prints `shape: notice: the comment was not posted (... HTTP 403)` and goes on: the
check result still decides the gate, and the verdict and the findings are in the step summary of the
job.

Use the `pull_request` event. Do **not** use `pull_request_target` to get a writable token: that event
runs with the base repository's secrets and permissions, and a workflow that checks out the pull
request's code under it runs untrusted code with them. Shape's own examples never do.

## How the action is written

The rules are checked by `tests/prbot/test_action.py`, which parses `action.yml`:

- Every action it uses is pinned to a full commit SHA.
- An input reaches a script only through `env:`, never through `${{ }}` inside a `run:` script, so a
  value such as a source name cannot become shell code. Source names and `shape-version` are
  validated again in the script.
- No step echoes the token; only the step that posts the comment has it in its environment.

The same test runs the action's scripts against the fixture projects in `tests/fixtures/action/`.
`.github/workflows/action-selftest.yml` runs `uses: ./` on them in every pull request that touches
the action: once expecting `verdict: pass`, once expecting `verdict: fail` with the failing job step
tolerated and asserted.

## The status badge

See [CI.md](CI.md#status-badge) for `shape badge` and for publishing it from the same workflow.

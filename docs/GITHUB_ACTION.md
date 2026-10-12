# GitHub Action walkthrough

Gate a pull request on a project's current data matching its baseline.

Status: available.

The shipped composite `action.yml` profiles each named source, compares it with its baseline,
collects result documents and produces a verdict. `fail-on: drift` fails for drift or a command
failure. `comment: false` keeps the example account-free at the command level. Enabling comments
needs pull-request write permission and sends the selected report to GitHub.

## 1. Choose a project

The complete committed sample is `tests/fixtures/action/pass`: source CSV, baseline shape and
project configuration. Its shape.yml is:

```yaml
format: shape-project
version: 1
name: action-fixture
sources:
  orders:
    path: data/orders.csv
    baseline:
      kind: pinned
      artifact: baseline/orders.shape
```

## 2. Add the workflow

Save this complete configuration as a workflow file. It is configuration, not an executed cloud
command transcript. The floating `main` action ref selects shipped code; pin a reviewed commit
for your production use. This docs PR does not create or change branch protection settings.

```yaml
name: Shape drift check
on:
  pull_request:
permissions:
  contents: read
jobs:
  shape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sqllocks/shape@main
        with:
          shape-version: '0.9.1'
          project: tests/fixtures/action/pass
          fail-on: drift
          comment: 'false'
```

## Hosted run

The [demo repository](https://github.com/sqllocks/shape-action-demo) supplies hosted receipts from 2026-10-11. Its workflow uses `sqllocks/shape@v0.9.1` with `comment: 'true'` and `pull-requests: write`. The example above uses `sqllocks/shape@main` with `comment: 'false'`; the local execution below is a separate receipt.

| Pull request | Hosted run / job | Posted result |
|---|---|---|
| [#1, README-only change](https://github.com/sqllocks/shape-action-demo/pull/1) | [38180056187 / shape](https://github.com/sqllocks/shape-action-demo/actions/runs/38180056187/job/114590509077) succeeded | pass: 1 source, 0 findings; Shape 0.9.1 |
| [#2, changed order totals](https://github.com/sqllocks/shape-action-demo/pull/2) | [38180286381 / shape](https://github.com/sqllocks/shape-action-demo/actions/runs/38180286381/job/114591198353) failed in 19 seconds | fail: 1 source, 1 finding; Shape 0.9.1 |

PR #2 reported a medium-severity `null_rate_change` finding for `order_total`. The [pass receipt](evidence/github-action-pass.md) and [drift receipt](evidence/github-action-drift.md) preserve the job IDs, timestamps, log excerpts and bot comments.

## Local action execution

The composite shell steps ran against the committed pass fixture, with Shape already installed
and comments disabled. This is their complete output; it is not a hosted workflow receipt.

??? info "Local action output (exit 0)"

    ```text
    Install Shape
    shape 0.9.1

    Check the project
    shape: note: /tmp/tmp7ajhd59t/shape-action/current/orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: /tmp/tmp7ajhd59t/project/baseline/orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)

    Gate on the result
    Shape verdict: pass (fail-on: drift)
    ```

## 3. Read the verdict

A pass has zero findings. A drift verdict has reported changes; with the selected fail-on policy
it fails the step. A fail verdict means an operation or gate fails. Do not replace a baseline
just to turn the check green. See [When drift goes red](DRIFT_RED.md).

The action outputs `verdict`, `findings` and `comment-path`. Comments on fork pull requests can
be skipped when the token is read-only. Do not run untrusted contributor code with write secrets.
Require the job name `shape`, as GitHub lists it: the demo repository’s active [ruleset 24896836](https://github.com/sqllocks/shape-action-demo/rules/24896836) requires the context `shape`. These docs do not configure branch protection for your repository. See the [verified ruleset receipt](evidence/github-action-ruleset.md) and [Owner decision](DECISIONS.md#required-status-check-hosted).

## What's next

Use your reviewed source and baseline instead of the fixture, and keep the project's paths
relative to its directory. Review all generated reports before making them public.

## Related

[Drift tutorial](tutorials/03-drift.md) · [Project file](PROJECT.md) · [What leaves my machine](WHAT_LEAVES.md)

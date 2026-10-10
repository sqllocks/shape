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

<!-- owner: CI maintainer — supply a hosted workflow transcript from a GitHub account. The local Action logic is tested below without posting a comment. -->

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
<!-- owner: repository owner — select the required status check if you want to gate merges. -->

## What's next

Use your reviewed source and baseline instead of the fixture, and keep the project's paths
relative to its directory. Review all generated reports before making them public.

## Related

[Drift tutorial](tutorials/03-drift.md) · [Project file](PROJECT.md) · [What leaves my machine](WHAT_LEAVES.md)

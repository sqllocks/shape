# Hosted GitHub Action required-check receipt

Captured via read-only GitHub tools on 2026-10-11T18:57:57-04:00; Owner decision dated 2026-10-11. API timestamps retain their original offsets.

- Repository: [https://github.com/sqllocks/shape-action-demo](https://github.com/sqllocks/shape-action-demo).
- Ruleset: [24896836](https://github.com/sqllocks/shape-action-demo/rules/24896836), `main requires Shape check`.
- Source GET: `https://api.github.com/repos/sqllocks/shape-action-demo/rulesets/24896836`.
- Created: `2026-10-11T18:23:16.396-04:00`; updated: `2026-10-11T18:23:16.452-04:00`.
- Enforcement `active`, target `branch`, condition `~DEFAULT_BRANCH`.
- Rules: deletion protection, non-fast-forward protection, and required status checks. The required context is `shape`, integration ID `15368` (GitHub Actions in the Owner-provided evidence).

## Retrieved ruleset excerpt

```json
{
  "id": 24896836,
  "name": "main requires Shape check",
  "target": "branch",
  "source": "sqllocks/shape-action-demo",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": [
        "~DEFAULT_BRANCH"
      ]
    }
  },
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {
            "context": "shape",
            "integration_id": 15368
          }
        ]
      }
    }
  ],
  "created_at": "2026-10-11T18:23:16.396-04:00",
  "updated_at": "2026-10-11T18:23:16.452-04:00"
}
```

## Required job and blocked PR

- Pass run [38180056187](https://github.com/sqllocks/shape-action-demo/actions/runs/38180056187), job `shape`, ID [114590509077](https://github.com/sqllocks/shape-action-demo/actions/runs/38180056187/job/114590509077): success; `2026-10-11T22:20:55Z` to `2026-10-11T22:21:19Z`.
- Drift run [38180286381](https://github.com/sqllocks/shape-action-demo/actions/runs/38180286381), job `shape`, ID [114591198353](https://github.com/sqllocks/shape-action-demo/actions/runs/38180286381/job/114591198353): failure; `2026-10-11T22:24:21Z` to `2026-10-11T22:24:40Z` (19 seconds).
- PR [#2](https://github.com/sqllocks/shape-action-demo/pull/2) GET: `state: open`, `merged: false`, `mergeable: true`, `mergeable_state: blocked`. The `mergeable` field concerns Git conflicts; it does not mean requirements permit merging.

Selected terminal log lines from the separately retrieved jobs:

```text
2026-10-11T22:21:17.2291128Z Shape verdict: pass (fail-on: drift)
2026-10-11T22:24:38.8845172Z Shape verdict: fail (fail-on: drift)
2026-10-11T22:24:38.8857255Z ##[error]Process completed with exit code 1.
```

The active rule requires `shape`, and PR #2 has that failed job and a blocked merge state. The Owner’s quoted merge-box wording was not independently captured as rendered UI; the API rule, job and PR state above supply the machine-readable evidence. Both PRs remain open and unmerged.

## Owner decision

> Required status check: the `shape` job; demonstrated in sqllocks/shape-action-demo ruleset 24896836; 2026-10-11

This demonstrates the demo repository’s configuration. It neither changes nor asserts branch protection for a reader’s repository.

## Release-checklist limits

`docs/RELEASE_CHECKLIST.md:160` (example 2) requests the annotated `v1.0.0` tag and push transcript, followed by release publication. These pull-request drift jobs do not supply that transcript.

`docs/RELEASE_CHECKLIST.md:184` (example 3) requests `sha256sum -c SHA256SUMS` from release-sbom and `gh attestation verify sqllocks_shape-1.0.0-py3-none-any.whl --repo sqllocks/shape`. Neither operation is present in these job logs. Both notes stay open.

## Retrieval limits

The generic GitHub fetch tool rejected `/actions/jobs/{job_id}` as an unsupported URL before any response. Raw job timestamps were instead read successfully from the allowed `/actions/runs/{run_id}/jobs` GETs. No permissions, rulesets, runs or PRs were changed.

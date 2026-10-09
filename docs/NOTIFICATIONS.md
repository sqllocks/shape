# Webhook notifications

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


When a scheduled check finds drift, someone has to know. After `shape diff`, `check`, `verify` or
`fidelity` has decided its result, Shape can POST one JSON document to a webhook. It is a generic
webhook: there are no integrations for particular chat or email services; point it at your own
receiver, or at anything that accepts a JSON POST. Everything runs in your CI; there is no hosted
service.

A notification never changes what a command does or returns. A target that cannot be reached is a
warning on standard error, and the exit code stays the command's.

## Configure targets

In `shape.yml` ([PROJECT.md](PROJECT.md)):

```yaml
notifications:
  - url: env://SHAPE_DRIFT_WEBHOOK       # where to POST: a credential reference
    on: [fail, drift]                    # when
    secret: env://SHAPE_DRIFT_SECRET     # optional: sign the body
    commands: [diff, check]              # optional: only these (default: all four)
  - url: file://secrets/ops-webhook
    on: [always]
```

`url` and `secret` are `env://NAME` or `file://PATH` references (the credential references of
[SINKS.md](SINKS.md)); the address and the secret are never written in `shape.yml`, which is in git. A
`file://` secret must not be readable by other users. Any other value is refused by
`shape project validate`.

| `on` | The notification is sent when |
|---|---|
| `fail` | the command exited with anything but 0 (verdict `fail`) |
| `drift` | the result lists findings (verdict `drift`, or `fail` with findings) |
| `always` | every run |

Values may be combined; a target is sent at most once per run. `shape.yml` without the key behaves as
before.

For one run, add a target on the command line: `--notify REF` (repeatable) on `diff`, `check`,
`verify` and `fidelity`. It is sent on every run of that command, on top of the file's targets.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`shape notify test [--project DIR]` sends a notification with `"verdict": "test"` to **every**
configured target (whatever its `on` and `commands`), prints which were delivered and exits 1 if any
was not. It exits 2 when there is no project or the project lists no notifications.

`--dry-run` on the checking commands lists each notification as a `send` action; the target is its
reference (`notification env://SHAPE_DRIFT_WEBHOOK`), so the address is never shown, and nothing is
resolved or opened.

## The document

```json
{
  "format": "shape-notification",
  "version": 1,
  "command": "diff",
  "project": "orders-platform",
  "source": "customers",
  "verdict": "fail",
  "exit_code": 1,
  "counts": {"findings": 2, "planned": 1},
  "findings": [
    {"table": "customers", "column": "email", "kind": "null_rate_change", "severity": "high"},
    {"table": "customers", "column": "tier", "kind": "new_category", "severity": "medium"}
  ],
  "shape_version": "1.0.0",
  "time": "2026-10-03T12:00:00Z",
  "run_url": "https://github.com/acme/data/actions/runs/42"
}
```

| Key | Meaning |
|---|---|
| `format`, `version` | `shape-notification` and an integer, now `1`. A receiver should check both and ignore keys it does not know. Keys are only ever added within a version. |
| `command` | `diff`, `check`, `verify`, `fidelity`, or `notify test`. |
| `project` | The `name` of `shape.yml`, or `null`. |
| `source` | The source of `shape.yml` the command ran for, or `null`. |
| `verdict` | `pass`, `drift`, `fail`, or `test`. `fail`: the exit code is not 0. `drift`: exit 0 and findings. `pass`: neither. |
| `exit_code` | The command's exit code. |
| `counts` | `findings` (all of them) and `planned` (changes that a registered planned change covers; they are not findings). |
| `findings` | At most 200 findings, most severe first: table, column, kind and severity. Empty for `check` rules that name no column. |
| `shape_version` | The Shape that sent it. |
| `time` | UTC, to the second. |
| `run_url` | The GitHub Actions run (from `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY` and `GITHUB_RUN_ID`), or `null`. |

The document holds names and counts, never a data value: no baseline, no current value, no sample.
The request body is the document as compact JSON with sorted keys, UTF-8.

## The signature

With `secret`, the request carries

```
X-Shape-Signature-256: sha256=<hex>
```

where `<hex>` is the HMAC-SHA256 of the exact request body, keyed with the secret. Verify the bytes you
received, before parsing them. A receiver in Python:

```python
import hashlib
import hmac


def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# in a request handler:
#   if not verify(SECRET, request_body_bytes, request.headers["X-Shape-Signature-256"]):
#       return 401
```

`shape.cli.notify.sign` and `verify` are the same two functions. Without `secret` there is no header,
so a receiver that is reachable from the internet should always use one.

## Delivery

- **HTTPS only.** Plain HTTP is accepted for a loopback address (for tests). A redirect is not
  followed, and is not retried.
- 10 seconds per attempt.
- Up to 3 attempts, with a pause of 1 and then 2 seconds, after a connection error or a 5xx response.
  A 4xx response (the receiver refused it) is not retried.
- A failed delivery prints `shape: warning: notification to env://NAME (https://host) failed: ...`
  on standard error. The warning names the reference and the host, never the address (a webhook
  address often carries a secret in its path), and the command's exit code is unchanged.

Receivers should answer quickly with a 2xx and do their work afterwards: a slow receiver is waited for
up to 10 seconds, three times.

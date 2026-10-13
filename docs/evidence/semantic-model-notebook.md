# Semantic model profiling in a Fabric notebook — Owner-run receipt

Owner-run on **2026-10-12** (22:32–22:36 Eastern) in a Fabric notebook in workspace `shape-test`
(PySpark (Python) kernel, standard session), by the Owner, Jonathan Stewart. Resolves the owner
notes on [`semantic-model://`](../plugins/cloud-sources.md#semantic-models-semantic-model)
(cloud-sources example 0) and [`shape profile-model`](../plugins/fabric-commands.md#profile-model)
(fabric-commands example 4). All output below is copied from the Owner's notebook cells; nothing
was edited except wrapping. These two commands run only in a Fabric notebook, where `sempy` signs in
as the notebook's user, so a CI run cannot reproduce them (see
[the failed CI attempt](#why-this-is-not-a-ci-transcript)).

## Environment

| item | value |
|---|---|
| Notebook | Fabric notebook in workspace `shape-test`, PySpark (Python) kernel, standard session |
| Python | 3.13 (`/home/trusted-service-user/cluster-env/trident_env/lib/python3.13`) |
| `semantic-link-sempy` | 0.14.1, already installed in the notebook environment |
| Shape | **0.9.1** (`sqllocks-shape` 0.9.1 and `sqllocks-shape-fabric` 0.9.1, installed by the first cell) |
| Semantic model | `shape_sm` in `shape-test`; at the time of the run it held **one table, `publicholidays`**, and no relationships |

The `shape` script is not on the notebook's `PATH` (cell 3), so Shape was invoked as
`python -m shape` through `!{sys.executable} -m shape ...`. The page examples use `shape ...`; the
command, options and output are otherwise the same.

## 1. Install (cell 1)

```text
%pip install "sqllocks-shape-fabric[semantic-link]" sqllocks-shape
...
Successfully installed sqllocks-shape-0.9.1 sqllocks-shape-fabric-0.9.1
```

`semantic-link-sempy` 0.14.1 was already satisfied, from
`/home/trusted-service-user/cluster-env/trident_env/lib/python3.13/site-packages`. The pip log
between the first and last lines was not supplied and is not reproduced here.

## 2. The `shape` script is not on the PATH (cell 3)

```text
!shape --version; echo exit=$?
/usr/bin/sh: line 1: shape: command not found
exit=127
```

## 3. Version through the interpreter (cell 4)

```text
import sys
!{sys.executable} -m shape --version; echo exit=$?
shape 0.9.1
exit=0
```

## 4. `shape profile-model` — fabric-commands example 4 (cell 5)

```text
!{sys.executable} -m shape profile-model shape-test/shape_sm -o retail.shape --max-rows 100000 --json; echo exit=$?
{"written": "retail.shape", "shape_content_id": "0c2911b8b03d9b899654b1a1c8c42d3a8f21ce7807677b6809c5d8f0c14d2e66", "workspace": "shape-test", "model": "shape_sm", "tables": 1, "relationships": 0, "max_rows": 100000}
exit=0
```

The page's example is `shape profile-model Sales/Retail -o retail.shape [--tables Customer,Orders]
[--max-rows 100000] [--json]`; the run used the test workspace and model, `--max-rows 100000` and
`--json`, and no `--tables`.

## 5. The model's tables, read with sempy directly (cell 6)

```text
import sempy.fabric as fabric
print(fabric.list_tables("shape_sm", workspace="shape-test")[["Name"]].to_string(index=False))
          Name
publicholidays
```

## 6. `semantic-model://` source — cloud-sources example 0 (cell 7)

```text
!{sys.executable} -m shape profile "semantic-model://shape-test/shape_sm/publicholidays" -o customer.shape; echo exit=$?
{"shape_content_id": "d79f97dbd9ef2c41b2340cf04e10742b2da15b14c11007b18356655b998c557a", "written": "customer.shape"}
exit=0
```

The page's example is `shape profile semantic-model://Sales/Retail/Customer -o customer.shape`.
The Owner replaced the `TABLE` part with `publicholidays`, the only table in `shape_sm`, and kept
the page's output file name `customer.shape`; the profiled table was `publicholidays`, not a
customer table.

## Why this is not a CI transcript

The same two commands were run from GitHub Actions as the CI service principal
(`.github/workflows/fabric-live.yml`, transcripts mode): `shape profile-model` in run 38320894194
and `shape profile semantic-model://...` in run 38327274076. Both failed with the same error,
before reading any data:

```text
shape: error: semantic-model:// needs sempy (pip install 'sqllocks-shape-fabric[semantic-link]') and runs inside a Fabric notebook: lack of valid FabricAnalyticsTokenCredentialProvider
```

Shape documents only the notebook-user sign-in for this source (`--auth` does not apply), so these
two examples are recorded from the notebook run above.

## Related

[Semantic models: `semantic-model://`](../plugins/cloud-sources.md#semantic-models-semantic-model) ·
[`profile-model`](../plugins/fabric-commands.md#profile-model) · [Published receipts](index.md)

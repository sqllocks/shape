# Library Management for the Shape User Data Functions item

Open the User Data Functions item > **Library management** (Home ribbon or the left pane).

The User Data Functions runtime is **Python 3.11** (3.12 when testing in the portal). Every public
library below must have a Python 3.11 wheel.

## Public libraries (from PyPI)

Pin exactly these versions (the owner's working set from the live dry run, 2026-10-05):

| Library | Version | Why |
|---|---|---|
| `numpy` | `2.4.6` | Shape's numerical core (`numpy>=2,<3`); the newest 2.x with a Python 3.11 wheel on 2026-10-05 |
| `pyarrow` | `19.0.1` | file parsing (`csv`, `json`, `parquet`) and Shape's tables; `fabric-user-data-functions` 1.0.142 requires `pyarrow>=19.0.1,<20` |
| `pandas` | `3.0.6` | `profileDataFrame`, `profileLakehouseTable` and `generateSample` |

`fabric-user-data-functions` is provided by the platform. This set resolves together with
`fabric-user-data-functions` 1.0.142 and the Shape wheel on Python 3.11, and every helper behind
the functions runs on it (checked locally in a Python 3.11 venv with exactly these versions;
the implementation tests, F-6).

## Private libraries (the Shape wheel: upload the file, never resolve it by name)

Add `sqllocks_shape-0.9.1-py3-none-any.whl` (build it with `python scripts/build_pure_wheel.py`,
or take it from the release) under **Add from local** (upload the `.whl` file). Constraints
(Microsoft Learn, retrieved 2026-09-30, COMPLETION_PLAN section 12.1):

* private libraries must be **platform-independent `.whl` files** (`py3-none-any`);
* each must be **under 28.6 MB**. Shape's pure wheel is checked against this by DM-03.

**Never list `sqllocks-shape` (or `sqllocks-shape==0.9.1`) as a public library.** PyPI has an
older `sqllocks-shape` 0.9.0 without `shape.integrations`; the functions import
`shape.integrations.fabric.udf`, so with that package every call fails with
`ModuleNotFoundError: No module named 'shape.integrations'`. The uploaded private wheel is the
only source of Shape for this item. **[VERIFY LIVE]** in the publish log that no `sqllocks-shape`
is downloaded from PyPI.

### `generateSample` needs a domain

`generateSample` generates from an installed domain, and a domain is a plugin. Also upload the
`sqllocks-shape-domains` wheel `sqllocks_shape_domains-0.9.1-py3-none-any.whl` (`pip wheel --no-deps plugins/shape-domains`) under
**Add from local**; it is `py3-none-any` and about 2 MB. It requires `sqllocks-shape==0.9.1`,
which the uploaded Shape wheel satisfies. **[VERIFY LIVE]** that the library resolver takes the
private Shape wheel for that requirement rather than PyPI's package of the same name and version
(the publish log shows no `sqllocks-shape` download, and `generateSample` returns rows). Without the
domains wheel the function raises `no domain named 'retail' (installed: none installed)`.
`generateSample` caps `rows` at 500,000 and cuts the response to the leading rows that fit in
25 MB of JSON (limit: 30 MB).

`function_app.py` is a thin template: the logic is `shape.integrations.fabric.udf` inside the wheel, so
upgrading the wheel upgrades the functions. CI (`pure-wheel`) builds the wheel, checks the tag and size,
and runs the UDF tests against it with `SHAPE_KERNEL=python`.

Publish the item after changing libraries; the change takes effect after the publish
finishes.

## Data connection

Under **Manage connections** add the demo lakehouse with the alias **`shapeLakehouse`**. The four
`@udf.connection("shapeLakehouse", "lakehouse")` decorators in `function_app.py` hold that alias
as a string literal, because the portal reads it from the file statically (an imported constant is
not seen); it equals `shape.integrations.fabric.udf.LAKEHOUSE_ALIAS`, and a test checks both. To
use another alias, replace the literal in all four decorators.
User Data Functions cannot use a service principal or managed identity for this
connection: it runs as the invoking user, who needs access to the lakehouse.

## Limits to remember

* 240 s execution (100 s via the public endpoint), 4 MB request, 30 MB response.
* `profileLakehouseFile` refuses files over `maxMegabytes` (default 50).
* `profileLakehouseTable` reads at most `maxRows` rows (default 1,000,000) and returns
  `sampled: true` when it hit the cap.
* Function parameter names are camelCase; `lakehouse` is injected by the connection.

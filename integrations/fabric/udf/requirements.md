# Library Management for the Shape User Data Functions item

Open the User Data Functions item > **Library management** (Home ribbon or the left pane).

## Public libraries (from PyPI)

| Library | Version | Why |
|---|---|---|
| `numpy` | `>=2.0,<3` | Shape's numerical core |
| `pyarrow` | `>=14` | file parsing (`csv`, `json`, `parquet`) and Shape's tables |
| `pandas` | `>=2.0` | `profileDataFrame` and `profileLakehouseTable` |

`fabric-user-data-functions` is provided by the platform. The runtime is **Python 3.11**
(3.12 when testing in the portal).

## Private library (the Shape wheel)

Add `sqllocks_shape-0.9.0-py3-none-any.whl` (build it with `python scripts/build_pure_wheel.py`) under **Add from local**. Constraints
(Microsoft Learn, retrieved 2026-09-30, COMPLETION_PLAN section 12.1):

* private libraries must be **platform-independent `.whl` files** (`py3-none-any`);
* each must be **under 28.6 MB**. Shape's pure wheel is checked against this by DM-03;
* once Shape is on PyPI you can list `sqllocks-shape==0.9.0` as a public library instead.

`function_app.py` is a thin template: the logic is `shape.integrations.fabric.udf` inside the wheel, so
upgrading the wheel upgrades the functions. CI (`pure-wheel`) builds the wheel, checks the tag and size,
and runs the UDF tests against it with `SHAPE_KERNEL=python`.

Publish the item after changing libraries; the change takes effect after the publish
finishes.

## Data connection

Under **Manage connections** add the demo lakehouse with the alias **`shapeLakehouse`**
(the name `function_app.py` binds to; it is imported from `shape.integrations.fabric.udf`; replace it with your alias string if you use another).
User Data Functions cannot use a service principal or managed identity for this
connection: it runs as the invoking user, who needs access to the lakehouse.

## Limits to remember

* 240 s execution (100 s via the public endpoint), 4 MB request, 30 MB response.
* `profileLakehouseFile` refuses files over `maxMegabytes` (default 50).
* `profileLakehouseTable` reads at most `maxRows` rows (default 1,000,000) and returns
  `sampled: true` when it hit the cap.
* Function parameter names are camelCase; `lakehouse` is injected by the connection.

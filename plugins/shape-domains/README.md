# sqllocks-shape-domains

Shape plugin: industry domains with their reference data (`shape.domains`).

| Domain | Tables | Reference data |
|---|---|---|
| `retail` | customer, address, product_category, product, store, promotion, order, order_line, return (3NF) | product categories, product and promotion names, 40,977 US ZIP locations |

```python
from shape.generation.domains import load_domain
from shape.generation.engine import Engine

domain = load_domain("retail")  # schema + reference data registered
tables = Engine(domain.schema, scale="medium", seed=1).generate().tables
```

Scales: `small`, `medium`, `large` and `xlarge` (and the other presets in the schema). The ZIP
locations are derived from GeoNames (CC BY 4.0); see `THIRD_PARTY_NOTICES.md` in the repository.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

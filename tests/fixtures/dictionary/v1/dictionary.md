# Data dictionary: orders

Project source: `orders`

## orders

200 rows; key: `id`

| Column | Type | Null rate | Distinct (est.) | Semantic label | Class | Numeric range | Length range | Format pattern | Owner | Annotations | Examples | Top values |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `id` | integer | 0% | 200 |  | CONFIDENTIAL |  |  |  |  |  |  |  |
| `email` | string | 0% | 200 | email (1) | CONFIDENTIAL |  | 19 to 22 | email |  |  |  |  |
| `amount` | float | 2% | 40 |  | INTERNAL | 0 to 58.5 |  |  | finance-data@example.com | note: a \| b <c>, unit: EUR | 0, 58.5 | 0 (2.5%), 1.5 (2.5%) |

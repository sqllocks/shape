# sqllocks-shape-domains

Shape plugin: industry domains with their reference data (`shape.domains`).

| Domain | Tables | Reference data |
|---|---|---|
| `retail` | customer, address, product_category, product, store, promotion, order, order_line, return (3NF) | product categories, product and promotion names, 40,977 US ZIP locations |
| `capital_markets` | exchange, sector, industry, company, daily_price, dividend, earnings, insider_transaction, split, trade | exchanges, GICS sectors, index memberships, 123 S&P 500 constituents |
| `education` | department, course, instructor, student, course_section, academic_standing, enrollment, financial_aid, grade_appeal | aid types, course catalog, department names |
| `financial` | branch, transaction_category, customer, account, loan, card, loan_payment, statement, transaction, fraud_flag | branch, merchant and transaction category names; the retail ZIP locations |
| `pulse` | rider, driver, vehicle, trip (ride-hailing) | none |
| `real_estate` | neighborhood, agent, property, listing, showing, offer, transaction, inspection, appraisal | inspection items, neighborhoods, property types; the retail ZIP locations |
| `supply_chain` | warehouse, supplier, material, purchase_order, purchase_order_line, inventory, shipment, shipment_event, quality_inspection, demand_forecast | carrier names, material categories, shipping methods; the retail ZIP locations |
| `telecom` | plan, device_model, subscriber, service_line, usage_record, billing, payment, network_event, churn_indicator | device models, network event types, plan types; the retail ZIP locations |

```python
from shape.generation.domains import load_domain
from shape.generation.engine import Engine

domain = load_domain("retail")  # schema + reference data registered
tables = Engine(domain.schema, scale="medium", seed=1).generate().tables
```

Scales: `small`, `medium`, `large` and `xlarge` (and the other presets in the schema). every domain except `retail`
also offers a `star` schema (`load_domain(name, mode="star")`). The ZIP locations are
derived from GeoNames (CC BY 4.0), and the other reference data is carried over under the MIT license;
see `THIRD_PARTY_NOTICES.md` in the repository.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.

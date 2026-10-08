-- A singular test returns the rows that violate the rule.
select *
from {{ ref('orders') }}
where amount < 0

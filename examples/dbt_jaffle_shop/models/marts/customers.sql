with orders as (
    select * from {{ ref('orders') }}
)
select
    c.customer_id::bigint as customer_id,
    c.first_name::varchar as first_name,
    c.last_name::varchar as last_name,
    min(o.order_date)::date as first_order,
    max(o.order_date)::date as most_recent_order,
    count(o.order_id)::bigint as number_of_orders,
    coalesce(sum(o.amount), 0)::decimal(18, 2) as customer_lifetime_value
from {{ ref('stg_customers') }} as c
left join orders as o on o.customer_id = c.customer_id
group by c.customer_id, c.first_name, c.last_name

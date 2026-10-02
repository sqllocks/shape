with payments as (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
)
select
    o.order_id::bigint as order_id,
    o.customer_id::bigint as customer_id,
    o.order_date::date as order_date,
    o.status::varchar as status,
    coalesce(p.amount, 0)::decimal(18, 2) as amount
from {{ ref('stg_orders') }} as o
left join payments as p on p.order_id = o.order_id

select payment_id, order_id, payment_method, amount
from {{ source('raw', 'raw_payments') }}

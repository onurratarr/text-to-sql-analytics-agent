select order_id, amount_paid, amount_refunded
from {{ ref('fct_orders') }}
where status = 'returned' and abs(amount_paid - amount_refunded) > 0.01

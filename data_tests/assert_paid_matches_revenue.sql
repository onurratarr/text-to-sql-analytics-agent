-- Completed and shipped orders must be fully paid (within a cent). Returns failing rows.
select order_id, net_revenue, amount_paid
from {{ ref('fct_orders') }}
where status in ('completed', 'shipped') and abs(net_revenue - amount_paid) > 0.01

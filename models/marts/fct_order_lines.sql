-- One row per order line, with the order's status/date so category and product questions can be answered.
select
    l.order_item_id,
    l.order_id,
    o.customer_id,
    o.order_date,
    o.order_month,
    o.status,
    l.product_id,
    l.product_name,
    l.category,
    l.quantity,
    case when o.status in ('completed', 'shipped') then l.net_amount else 0 end as net_revenue
from {{ ref('int_order_lines') }} l
join {{ ref('fct_orders') }} o using (order_id)

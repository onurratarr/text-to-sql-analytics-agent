select
    order_month,
    count(*)                                                        as orders,
    count(*) filter (where status in ('completed', 'shipped'))      as revenue_orders,
    count(*) filter (where status = 'cancelled')                    as cancelled_orders,
    round(sum(net_revenue), 2)                                      as net_revenue,
    round(sum(net_revenue) / nullif(count(*) filter (where status in ('completed', 'shipped')), 0), 2) as avg_order_value
from {{ ref('fct_orders') }}
group by order_month

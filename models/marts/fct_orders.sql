-- One row per order. net_revenue counts only completed or shipped orders (cancelled/returned/pending = 0).
with lines as (
    select order_id, count(*) as line_count, sum(quantity) as units,
           sum(gross_amount) as gross_amount, sum(net_amount) as net_amount
    from {{ ref('int_order_lines') }}
    group by order_id
)
select
    o.order_id,
    o.customer_id,
    o.order_date,
    cast(date_trunc('month', o.order_date) as date)          as order_month,
    o.status,
    o.channel,
    l.line_count,
    l.units,
    round(l.gross_amount, 2)                                 as gross_amount,
    round(l.gross_amount - l.net_amount, 2)                  as discount_amount,
    case when o.status in ('completed', 'shipped') then round(l.net_amount, 2) else 0 end as net_revenue,
    coalesce(p.amount_paid, 0)                               as amount_paid,
    coalesce(p.amount_refunded, 0)                           as amount_refunded
from {{ ref('stg_orders') }} o
join lines l using (order_id)
left join {{ ref('int_order_payments') }} p using (order_id)

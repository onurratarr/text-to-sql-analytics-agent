-- Customer dimension for analytics. Email is deliberately not carried into the marts layer.
select
    c.customer_id,
    c.customer_name,
    c.country,
    c.segment,
    c.signup_date,
    min(o.order_date)                         as first_order_date,
    max(o.order_date)                         as last_order_date,
    count(o.order_id)                         as lifetime_orders,
    round(coalesce(sum(o.net_revenue), 0), 2) as lifetime_revenue
from {{ ref('stg_customers') }} c
left join {{ ref('fct_orders') }} o using (customer_id)
group by all

-- The monthly mart must add up to the order fact table.
select m.total, f.total
from (select round(sum(net_revenue), 2) as total from {{ ref('mart_monthly_revenue') }}) m,
     (select round(sum(net_revenue), 2) as total from {{ ref('fct_orders') }}) f
where abs(m.total - f.total) > 0.05

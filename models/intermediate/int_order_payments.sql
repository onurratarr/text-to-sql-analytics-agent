select
    order_id,
    sum(case when amount > 0 then amount else 0 end)  as amount_paid,
    -sum(case when amount < 0 then amount else 0 end) as amount_refunded,
    count(*)                                           as payment_count
from {{ ref('stg_payments') }}
group by order_id
